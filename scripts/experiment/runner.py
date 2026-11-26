#!/usr/bin/env python3
"""PoolNode 实验采集统一执行器（PN-RECOVERY-01）。

设计原则
--------
* 入口只有这一个文件：五个计划任务各自以 `--task <name>` 调用它。
* 实验逻辑仍来自 Git 中的正式脚本（`scripts/*.py`）；本文件只负责调度、
  预检、日志、状态、结果校验与回传，不重新实现任何测量逻辑。
* 任何"没跑"的情况都记为 SKIPPED（带原因），绝不冒充 SUCCESS。
* 每次运行产生独立 run_id / 日志 / 状态文件；重复 run_id 拒绝覆盖。

退出码：0=SUCCESS 1=FAILED 3=SKIPPED 4=PARTIAL 5=TIMEOUT
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import platform
import secrets
import socket
import ssl
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import locks  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOGS = ROOT / "reports" / "experiment-logs"
STATE_DIR = LOGS / "state"
SMOKE_DIR = LOGS / "smoke"
TELEMETRY_DIR = LOGS / "telemetry"


def _default_lock_dir() -> pathlib.Path:
    """锁必须跨 worktree 生效：同一台机器上两个 worktree 不能各锁各的。"""
    env = os.environ.get("POOL_LOCK_DIR")
    if env:
        return pathlib.Path(env)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or pathlib.Path.home()
        return pathlib.Path(base) / "PoolNode" / "locks"
    return pathlib.Path.home() / ".cache" / "poolnode" / "locks"


LOCK_DIR = _default_lock_dir()
LOCK_JOURNAL = LOGS / "locks.jsonl"
REMOTE_CONF = pathlib.Path(os.environ.get("POOL_REMOTE_CONF",
                                         pathlib.Path.home() / ".pool" / "remote.json"))
EXIT = {"SUCCESS": 0, "FAILED": 1, "SKIPPED": 3, "PARTIAL": 4, "TIMEOUT": 5}

# 任务定义：参数来自正式脚本 argparse 与历史日志（见 phase-0 报告 §3）
TASKS: dict[str, dict] = {
    "cmin": {
        "script": "scripts/cmin_experiment.py",
        # 五档正式窗口（任务书 §3.1）；1 点档仅存在于历史数据，不属计划
        "args": ["--only-hours", "12,20,21,22,23", "--dedupe-hour", "--reps", "5"],
        "hours": {12, 20, 21, 22, 23},
        "timeout": 1800,
        "out_style": "file",
        "output": "reports/benchmarks/cmin-experiment/runs.jsonl",
        "keys": ["ts", "cmin", "workers", "rep"],
        "sha_field": "sha_ok",
        "needs": ["config/bench-assets.yaml"],
        "origin": True,
        # 正式实验需单独授权（§4.3）；未授权前即便任务被触发也拒绝执行
        "suspended": True,
        "suspend_reason": "C_MIN 正式实验尚未获得窗口授权（PN-EXPERIMENT-01 §4.3）",
        "resources": ["link:local-hk", "out:cmin-experiment"],
        "telemetry": True,
        "smoke_args": ["--reps", "1", "--cmin", "98304"],
        "purpose": "C_MIN 96K/128K 背靠背配对（五档）",
    },
    "rcv05": {
        "script": "scripts/bench_v05_pairs.py",
        "args": ["--arms", "B,D", "--pairs", "5", "--workers", "16",
                 "--out", "reports/benchmarks/v05-rc1"],
        "hours": {20, 21, 22, 23},
        "timeout": 2400,
        "out_style": "dir",
        "output": "reports/benchmarks/v05-rc1/runs.jsonl",
        "keys": ["ts", "arm", "round"],
        "sha_field": "sha_ok",
        "needs": ["config/bench-assets.yaml"],
        "origin": True,
        "resources": ["link:local-hk", "out:v05-rc1"],
        "smoke_args": ["--arms", "B,D", "--pairs", "1", "--workers", "4"],
        "purpose": "v0.4 static vs v0.5 auto 配对（B/D）",
    },
    "rcmatrix": {
        # 实验脚本在 investigate 分支的独立 worktree：不复制，按路径引用并校验
        "script": "../Pool-investigate/scripts/rc_matrix.py",
        "args": ["--only-hours", "20,21,22,23", "--dedupe-hour", "--reps", "5"],
        "hours": {20, 21, 22, 23},
        "timeout": 2400,
        "out_style": "dir",
        "output": "reports/benchmarks/v05-chunk-queue/runs.jsonl",
        "keys": ["ts", "arm", "round"],
        "sha_field": "sha_ok",
        "needs": ["config/bench-assets.yaml"],
        "origin": True,
        "resources": ["link:local-hk", "out:v05-chunk-queue"],
        "smoke_args": ["--reps", "1", "--force"],
        "purpose": "v0.5 分块队列 A(static16)/B(dyn16)/C(dyn21)",
    },
    "probe": {
        "script": "scripts/peak_probe.py",
        # --out 由 execute() 统一按 target 注入，避免与校验路径不一致
        "args": ["--tag", "local", "--asset", "bench-6m", "--arms", "single,w16,w32",
                 "--slots", "12,16,20,21,22,23", "--slot-guard"],
        "hours": {12, 16, 20, 21, 22, 23},
        "timeout": 1800,
        "out_style": "file",
        "output": "reports/benchmarks/v06-monitoring/local-probe.jsonl",
        "keys": ["ts"],
        "sha_field": None,
        "needs": ["config/bench-assets.yaml"],
        "origin": True,
        "resources": ["link:local-hk", "out:v06-monitoring"],
        "smoke_args": ["--tag", "local", "--asset", "bench-6m", "--arms", "single", "--force"],
        "purpose": "本机三臂探针（single/w16/w32）",
    },
    "sync": {
        "script": "scripts/collect_box_probe.py",
        "args": ["--host", os.environ.get("POOL_CN2_HOST", "160.202.47.187"),
                 "--user", os.environ.get("POOL_CN2_USER", "root")],
        "hours": {23},          # 每日 23:40；其他小时触发记为 SKIPPED
        "daily": True,
        "allow_empty": True,    # 远端没有新数据是正常情况，不算失败
        "timeout": 900,
        "out_style": "file",
        "output": "reports/benchmarks/v06-monitoring/cn2box-probe.jsonl",
        "keys": ["ts"],
        "sha_field": None,
        "needs": [],
        "origin": False,
        # 与正式实验共用本机↔香港出口（回传目的地同在香港），因此同样受链路锁约束
        "resources": ["link:local-hk", "ssh:cn2", "out:v06-monitoring"],
        "smoke_args": ["--host", os.environ.get("POOL_CN2_HOST", "160.202.47.187"),
                       "--user", os.environ.get("POOL_CN2_USER", "root")],
        "purpose": "CN2 探测机结果回收（只增不改）",
    },
}


def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def new_run_id(task: str) -> str:
    return f"{task}-{now():%Y%m%dT%H%M%S}-{secrets.token_hex(3)}"


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def count_lines(path: pathlib.Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


def write_json(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def target_path(task: dict, out_dir: pathlib.Path | None) -> pathlib.Path:
    """正式模式 -> task['output']；烟测模式 -> <out_dir>/runs.jsonl。"""
    if out_dir is None:
        return ROOT / task["output"]
    return out_dir / "runs.jsonl"


def origin_reachable(origin: str, timeout: float = 8.0) -> tuple[bool, str]:
    rest = origin.split("://", 1)[1].split("/", 1)[0]
    host, _, port_s = rest.partition(":")
    port = int(port_s) if port_s.isdigit() else (443 if origin.startswith("https") else 80)
    try:
        if origin.startswith("https"):
            ctx = ssl.create_default_context()
            with socket.create_connection((host, port), timeout=timeout) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ss:
                    return True, f"{host}:{port} tls={ss.version()}"
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} tcp"
    except Exception as exc:  # noqa: BLE001
        return False, f"{host}:{port} {exc.__class__.__name__}: {exc}"


# ---------------------------------------------------------------------------
def code_dirty() -> list[str]:
    """代码目录的未提交改动（不含实验数据/报告，故只看 scripts 与 tests）。"""
    proc = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no",
                           "--", "scripts", "tests"], cwd=ROOT,
                          capture_output=True, text=True)
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def precheck(task_name: str, task: dict, out_dir: pathlib.Path | None,
             override: dict, formal: bool = True) -> list[str]:
    problems: list[str] = []
    script = pathlib.Path(override["script"]) if override.get("script") else ROOT / task["script"]
    if not script.is_file():
        problems.append(f"script_missing:{script}")
    if formal and not override.get("skip_code_dirty"):
        dirty = code_dirty()
        if dirty:
            # commit 无法标识实际运行的代码，正式实验不接受这种状态（§4.1）
            problems.append(f"code_dirty:{len(dirty)}:"
                            + ";".join(d.split()[-1] for d in dirty[:5]))
    if sys.version_info < (3, 10):
        problems.append(f"python_too_old:{platform.python_version()}")
    if os.environ.get("POOL_FORCE_MISSING_DEP") == "1":
        problems.append("dependency_missing:PyYAML:forced-by-injection")
    else:
        try:
            import yaml  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            problems.append(f"dependency_missing:PyYAML:{exc.__class__.__name__}")
    for rel in task["needs"]:
        if not (ROOT / rel).is_file():
            problems.append(f"config_missing:{rel}")
    target = target_path(task, out_dir)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        probe = target.parent / f".write-test-{secrets.token_hex(4)}"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        problems.append(f"output_not_writable:{target.parent}:{exc.strerror or exc}")
    if task.get("origin") and not override.get("skip_origin"):
        origin = os.environ.get("POOL_ORIGIN", "https://dl.bszx.site")
        ok, detail = origin_reachable(origin)
        if not ok:
            problems.append(f"origin_unreachable:{detail}")
    if task_name == "sync":
        if not (pathlib.Path.home() / ".ssh").exists():
            problems.append("ssh_missing:~/.ssh")
    return problems


def validate_outputs(task: dict, target: pathlib.Path, before_lines: int,
                     corrupt: str | None) -> dict:
    report = {"path": str(target), "new_records": 0, "bad_json": 0, "missing_fields": 0,
              "duplicate_keys": 0, "sha_failures": 0, "ok": True, "detail": []}
    if not target.exists():
        report.update(ok=False, detail=[f"missing_output:{target}"])
        return report
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    new_lines = lines[before_lines:]
    if corrupt == "bad_jsonl":
        new_lines = new_lines + ['{"ts": "x", broken json']
    if corrupt == "sha_mismatch":
        new_lines = new_lines + [json.dumps({"ts": "2026-11-27T00:00:00", "cmin": 98304,
                                             "workers": 1, "rep": 0, "sha_ok": False})]
    seen: set[tuple] = set()
    for line in new_lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            report["bad_json"] += 1
            continue
        report["new_records"] += 1
        for key in task["keys"]:
            if key not in rec:
                report["missing_fields"] += 1
                break
        if task.get("sha_field") and rec.get(task["sha_field"]) is False:
            report["sha_failures"] += 1
        key = tuple(str(rec.get(k)) for k in task["keys"])
        if key in seen:
            report["duplicate_keys"] += 1
        seen.add(key)
    if report["bad_json"] or report["missing_fields"] or report["sha_failures"]:
        report["ok"] = False
    return report


# ---------------------------------------------------------------------------
def remote_upload(files: list[tuple[pathlib.Path, str]], dest_dir: str) -> dict:
    res = {"enabled": False, "ok": False, "uploaded": [], "skipped": [], "detail": "no-config"}
    if not REMOTE_CONF.is_file():
        return res
    try:
        import paramiko
    except Exception as exc:  # noqa: BLE001
        res["detail"] = f"paramiko_missing:{exc.__class__.__name__}"
        return res
    conf = json.loads(REMOTE_CONF.read_text(encoding="utf-8"))
    known = pathlib.Path(conf.get("known_hosts", ""))
    if not known.is_file():
        res.update(detail="known_hosts_missing")
        return res
    res["enabled"] = True
    cli = paramiko.SSHClient()
    cli.load_host_keys(str(known))
    cli.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        cli.connect(conf["host"], port=int(conf.get("port", 22)), username=conf["user"],
                    password=conf["password"], look_for_keys=False, allow_agent=False,
                    timeout=15, banner_timeout=15, auth_timeout=15)
    except Exception as exc:  # noqa: BLE001
        res["detail"] = f"connect_failed:{exc.__class__.__name__}"
        return res

    def run(cmd: str, timeout: float = 60) -> tuple[int, str]:
        chan = cli.get_transport().open_session()
        chan.settimeout(1.0)
        chan.exec_command(cmd)
        buf = bytearray()
        deadline = time.time() + timeout
        while True:
            if time.time() > deadline:
                chan.close()
                return 124, buf.decode("utf-8", "replace")
            if chan.recv_ready():
                buf.extend(chan.recv(65536))
                continue
            if chan.recv_stderr_ready():
                chan.recv_stderr(65536)
                continue
            if chan.exit_status_ready():
                while chan.recv_ready():
                    buf.extend(chan.recv(65536))
                return chan.recv_exit_status(), buf.decode("utf-8", "replace")
            time.sleep(0.02)

    try:
        rc, _ = run(f"mkdir -p {dest_dir}")
        if rc != 0:
            res["detail"] = "mkdir_failed"
            return res
        for f, remote_name in files:
            if not f.exists():
                continue
            local_sha = sha256_file(f)
            remote = f"{dest_dir}/{remote_name}"
            rc, out = run(f"if [ -f {remote} ]; then sha256sum {remote}; else echo MISSING; fi")
            if "MISSING" not in out:
                if local_sha in out:
                    res["skipped"].append(remote_name)  # 幂等：同名同内容
                    continue
                res["detail"] = f"remote_conflict:{remote_name}"
                return res
            data = f.read_bytes()
            chan = cli.get_transport().open_session()
            chan.settimeout(None)
            chan.exec_command(f"cat > {remote}")
            chan.sendall(data)
            chan.shutdown_write()
            if chan.recv_exit_status() != 0:
                res["detail"] = f"upload_failed:{remote_name}"
                return res
            rc, out = run(f"sha256sum {remote}")
            if local_sha not in out:
                res["detail"] = f"remote_sha_mismatch:{remote_name}"
                return res
            res["uploaded"].append(remote_name)
        res.update(ok=True, detail="ok")
        return res
    finally:
        cli.close()


# ---------------------------------------------------------------------------
def in_window(task: dict, moment: dt.datetime) -> bool:
    hours = task.get("hours")
    return hours is None or moment.hour in hours


def strip_out(args: list[str]) -> list[str]:
    """移除任务定义里已有的 --out（统一由 execute() 按 target 注入）。"""
    out, skip = [], False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg == "--out":
            skip = True
            continue
        if arg.startswith("--out="):
            continue
        out.append(arg)
    return out


def out_target(task: dict, target: pathlib.Path) -> str:
    """dir 型任务传目录（脚本自己拼 runs.jsonl），file 型任务传文件。"""
    return str(target.parent if task["out_style"] == "dir" else target)


def run_telemetry(rid: str) -> dict:
    """正式实验前的链路遥测（RTT/TCP/TLS/TTFB），失败只降级分类，不阻断实验。"""
    script = pathlib.Path(__file__).resolve().parent / "net_telemetry.py"
    out = TELEMETRY_DIR / f"{rid}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run([sys.executable, "-X", "utf8", str(script), "--out", str(out)],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=90)
    except Exception as exc:  # noqa: BLE001 - 遥测失败不得伪装成功
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
    return {"ok": proc.returncode == 0 and out.is_file(), "rc": proc.returncode,
            "path": str(out), "stderr": (proc.stderr or "").strip()[-200:]}


def execute(task_name: str, task: dict, rid: str, smoke: bool = False,
            override: dict | None = None) -> dict:
    override = override or {}
    started = now()
    out_dir = (pathlib.Path(override["out_dir"]) if override.get("out_dir")
               else ((SMOKE_DIR / rid) if smoke else None))
    if out_dir:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # 连输出目录都建不出来：仍要留下一条可读的失败记录
            LOGS.mkdir(parents=True, exist_ok=True)
            fail = {"task_name": task_name, "run_id": rid, "status": "FAILED",
                    "exit_code": 1, "start_time": started.isoformat(),
                    "end_time": now().isoformat(),
                    "error_summary": f"out_dir_not_writable:{exc}",
                    "record_count": 0, "upload_status": "not_attempted", "smoke": smoke}
            write_json(STATE_DIR / task_name / f"{rid}.json", fail)
            return fail
    target = target_path(task, out_dir)

    log_dir = LOGS / task_name / f"{started:%Y-%m-%d}"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{rid}.log"
    state_path = STATE_DIR / task_name / f"{rid}.json"

    state = {
        "task_name": task_name, "run_id": rid,
        "start_time": started.isoformat(), "end_time": None,
        "exit_code": None, "status": "RUNNING",
        "branch": subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT,
                                 capture_output=True, text=True).stdout.strip(),
        "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                 capture_output=True, text=True).stdout.strip(),
        "script_version": task["script"], "script_path": str(ROOT / task["script"]),
        "output_files": [], "record_count": 0, "sha256_status": None,
        "upload_status": "not_attempted", "error_summary": "",
        "smoke": smoke, "host": platform.node(), "log": str(log_path),
    }
    if state_path.exists():
        state.update(status="FAILED", exit_code=1, end_time=now().isoformat(),
                     error_summary="duplicate_run_id")
        write_json(state_path, state)
        return state
    write_json(state_path, state)

    def log(msg: str) -> None:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")

    def finish(status: str, summary: str = "", **extra) -> dict:
        state.update(end_time=now().isoformat(), status=status,
                     exit_code=EXIT[status], error_summary=summary, **extra)
        write_json(state_path, state)
        log(f"[RESULT] status={status} exit={EXIT[status]} summary={summary}")
        return state

    log(f"run_id={rid}\ntask={task_name}\nsmoke={smoke}\nstart={started.isoformat()}\n"
        f"commit={state['commit']} branch={state['branch']}\n"
        f"python={sys.version.split()[0]} {sys.executable}\n"
        f"origin={os.environ.get('POOL_ORIGIN', 'https://dl.bszx.site')}\n"
        f"target={target}")

    if not smoke and not in_window(task, started):
        return finish("SKIPPED", f"hour {started.hour} not in {sorted(task['hours'])}")

    # 未获授权的正式实验：即使任务被重新启用也不会产生流量（§4.3）
    if not smoke and task.get("suspended"):
        return finish("SKIPPED", f"suspended:{task.get('suspend_reason', '')}"[:400])

    try:
        lock_set = locks.hold(LOCK_DIR, LOCK_JOURNAL, task.get("resources", []),
                              task_name, rid)
    except locks.LockConflict as exc:
        log(f"[LOCK-CONFLICT] {exc.reason()}")
        return finish("SKIPPED", exc.reason()[:400], phase="lock")
    except locks.LockError as exc:
        log(f"[LOCK-ERROR] {exc}")
        return finish("FAILED", f"lock_error:{exc}"[:400], phase="lock")

        state["locks"] = lock_set.describe()
        state["code_dirty"] = [] if smoke else code_dirty()[:20]
        if state["code_dirty"]:
            write_json(state_path, state)
    try:
        problems = precheck(task_name, task, out_dir, override, formal=not smoke)
        if problems:
            log("[PRECHECK-FAIL]\n" + "\n".join(problems))
            return finish("FAILED", "; ".join(problems)[:400], phase="precheck")

        if task.get("telemetry") and not smoke:
            tel = run_telemetry(rid)
            state["telemetry"] = tel
            log("[TELEMETRY] " + json.dumps(tel, ensure_ascii=False))

        base = list(task["smoke_args"] if smoke else task["args"])
        args = strip_out(base) + ["--out", out_target(task, target)]
        state["out_arg"] = out_target(task, target)
        interpreter = override.get("interpreter", sys.executable)
        cmd = [interpreter, "-X", "utf8", str(pathlib.Path(override["script"])
                                             if override.get("script")
                                             else ROOT / task["script"])] + args
        if override.get("extra_args"):
            cmd += list(override["extra_args"])

        before = count_lines(target)
        log(f"[CMD] {' '.join(cmd)}\n[EXEC] start {now():%H:%M:%S}")

        timeout = float(override.get("timeout", task["timeout"]))
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout)
            out, err, rc = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            elapsed = time.perf_counter() - t0
            log(f"[TIMEOUT] after {elapsed:.1f}s\n{exc.stdout or ''}\n{exc.stderr or ''}")
            return finish("TIMEOUT", f"timeout after {elapsed:.1f}s")
        except OSError as exc:
            log(f"[SPAWN-FAIL] {exc.__class__.__name__}: {exc}")
            return finish("FAILED", f"spawn_failed:{exc.__class__.__name__}:{exc}")
        elapsed = time.perf_counter() - t0
        log(f"[EXEC] rc={rc} elapsed={elapsed:.1f}s\n--- stdout ---\n{out}\n--- stderr ---\n{err}")

        if rc == 0 and "skip:" in out.lower() and not out.strip().startswith("{"):
            line = next((l for l in out.splitlines() if "skip:" in l), "skip")
            return finish("SKIPPED", line[:200], rc_raw=rc)

        if rc != 0:
            tail = (err or out).strip().splitlines()
            return finish("FAILED", (tail[-1][:300] if tail else f"rc={rc}"), rc_raw=rc,
                          elapsed_s=round(elapsed, 1))

        report = validate_outputs(task, target, before, override.get("corrupt"))
        state.update(output_files=[report["path"]], record_count=report["new_records"],
                     sha256_status=("ok" if report["sha_failures"] == 0
                                    else f"fail:{report['sha_failures']}"),
                     validation=report, elapsed_s=round(elapsed, 1))
        if not report["ok"]:
            log("[VALIDATE-FAIL] " + json.dumps(report, ensure_ascii=False))
            return finish("FAILED", "validation: " + (report["detail"][0] if report["detail"]
                                                      else f"bad_json={report['bad_json']} "
                                                           f"sha_fail={report['sha_failures']}"))
        if report["new_records"] == 0 and not smoke and not task.get("allow_empty"):
            return finish("FAILED", "no_new_records_in_window")

        uploads = [(log_path, log_path.name),
                   (state_path, state_path.name),
                   (target, f"{rid}-{target.name}")]
        tel_path = state.get("telemetry", {}).get("path")
        if tel_path and pathlib.Path(tel_path).is_file():
            uploads.append((pathlib.Path(tel_path), f"{rid}-telemetry.json"))
        dest = f"/srv/pool/labnode/experiment-inbox/{'smoke/' if smoke else ''}{task_name}/{started:%Y-%m-%d}"
        # 上传前先落"最终状态"：否则远端归档里的状态文件永远是 RUNNING
        state.update(status="SUCCESS", exit_code=EXIT["SUCCESS"],
                     end_time=now().isoformat(), upload_status="pending")
        write_json(state_path, state)
        log("[UPLOAD] start " + dest)
        up = remote_upload(uploads, dest)
        state["upload_status"] = ("ok" if up["ok"] else
                                  ("skipped:" + up["detail"] if not up["enabled"] else "failed:" + up["detail"]))
        log("[UPLOAD] " + json.dumps(up, ensure_ascii=False))
        if up["enabled"] and not up["ok"]:
            return finish("PARTIAL", f"upload:{up['detail']}", upload=up)
        return finish("SUCCESS", f"records+{report['new_records']}", upload=up)
    finally:
        lock_set.release()


# ---------------------------------------------------------------------------
def smoke_all() -> int:
    results = {}
    for name in TASKS:
        rid = new_run_id(f"smoke-{name}")
        st = execute(name, TASKS[name], rid, smoke=True)
        results[name] = {"status": st["status"], "run_id": rid,
                         "summary": st["error_summary"][:160],
                         "records": st.get("record_count"),
                         "upload": st.get("upload_status")}
    write_json(SMOKE_DIR / f"smoke-summary-{now():%Y%m%dT%H%M%S}.json",
               {"generated_at": now().isoformat(), "results": results})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(v["status"] in ("SUCCESS", "SKIPPED") for v in results.values()) else 1


def inject(case: str) -> int:
    """故障注入：验证执行器不会产生伪成功（只写 smoke 目录）。"""
    global REMOTE_CONF
    name = "cmin"
    task = TASKS[name]
    rid = new_run_id(f"inject-{case}")
    ov: dict = {}
    if case == "missing_script":
        ov["script"] = str(ROOT / "scripts" / "does-not-exist.py")
    elif case == "bad_interpreter":
        ov["interpreter"] = "C:/no/such/python.exe"
    elif case == "missing_dep":
        os.environ["POOL_FORCE_MISSING_DEP"] = "1"
    elif case == "unwritable_out":
        ov["out_dir"] = "C:/Windows/System32/pool-exp-test"
    elif case == "offline":
        os.environ["POOL_ORIGIN"] = "https://127.0.0.1:1"
    elif case == "remote_down":
        bad = SMOKE_DIR / rid / "remote.json"
        write_json(bad, {"host": "127.0.0.1", "port": 1, "user": "x", "password": "x",
                         "known_hosts": str(pathlib.Path.home() / ".ssh" / "pool-lab"
                                            / "known_hosts_hk_lab")})
        REMOTE_CONF = bad
    elif case == "timeout":
        ov["timeout"] = 0.05
    elif case in ("bad_jsonl", "sha_mismatch"):
        ov["corrupt"] = case
    elif case == "duplicate_run_id":
        st1 = execute(name, task, rid, smoke=True)
        st2 = execute(name, task, rid, smoke=True)
        print(json.dumps({"case": case, "first": st1["status"], "second": st2["status"],
                          "second_summary": st2["error_summary"]}, ensure_ascii=False))
        return 0 if st2["status"] == "FAILED" and st2["error_summary"] == "duplicate_run_id" else 1
    else:
        print(f"unknown case {case}")
        return 2

    try:
        st = execute(name, task, rid, smoke=True, override=ov)
    finally:
        os.environ.pop("POOL_FORCE_MISSING_DEP", None)
    print(json.dumps({"case": case, "status": st["status"], "exit_code": st["exit_code"],
                      "summary": st["error_summary"][:200]}, ensure_ascii=False))
    return 0 if st["status"] in ("FAILED", "TIMEOUT", "PARTIAL") else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PoolNode experiment collector")
    ap.add_argument("--task", choices=sorted(TASKS))
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--smoke-all", action="store_true")
    ap.add_argument("--inject", metavar="CASE")
    ap.add_argument("--run-id")
    args = ap.parse_args(argv)

    if args.smoke_all:
        return smoke_all()
    if args.inject:
        return inject(args.inject)
    if not args.task:
        ap.error("需要 --task / --smoke-all / --inject 之一")
    rid = args.run_id or new_run_id(args.task)
    st = execute(args.task, TASKS[args.task], rid, smoke=args.smoke)
    print(json.dumps({k: st[k] for k in ("task_name", "run_id", "status", "exit_code",
                                        "record_count", "sha256_status",
                                        "upload_status", "error_summary")},
                     ensure_ascii=False, indent=2))
    return st["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
