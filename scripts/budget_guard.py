#!/usr/bin/env python3
"""进程内流量硬预算（PN-EXPERIMENT-01-RUN-02 §A4）。

在**每个网络请求发出之前**预留其预计字节数；预留与已用之和超过上限时
立即拒绝该请求，并把预算置为终止态（之后所有请求都会被拒绝）。

设计取舍
--------
* 预留 = 该请求的完整预期响应体（整文件或该分段），偏保守；
* 请求一旦进入"已预留"状态就**不再释放**：无法可靠证明"字节没有发出"时，
  宁可多记也不漏记（任务书 A4 第 5 条）；
* 预留/提交/终止全部在同一把线程锁内完成，多 worker 并发不会同时穿过检查
  （第 4 条）；
* 每次提交都落盘状态文件，进程被强杀也能从文件看出停在哪里。

通过环境变量启用（runner 在拿到一次性授权后设置）::

    POOL_BUDGET_LIMIT=120000000
    POOL_BUDGET_STATE=<path>/budget.json

未设置时 ``from_env()`` 返回 None，测量脚本行为完全不变（探针任务不受影响）。
"""
from __future__ import annotations

import json
import os
import threading
import time


class BudgetExceeded(RuntimeError):
    """预算不足或预算已终止：该请求不得发出。"""


class Budget:
    def __init__(self, limit: int, state_path: str | None = None):
        self.limit = int(limit)
        self.state_path = state_path
        self._lock = threading.RLock()   # dump() 会在持锁时再取一次，必须可重入
        self.spent = 0            # 已确认占用（保守：按预留额计）
        self.reserved = 0         # 已预留、尚未提交
        self.requests = 0         # 通过的预留次数
        self.rejected = 0         # 被拒绝的请求数
        self.stopped = False
        self.stop_reason = ""
        self.started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    # -- 核心 --------------------------------------------------------------
    def reserve(self, nbytes: int) -> int:
        nbytes = int(nbytes)
        if nbytes < 0:
            raise ValueError("negative reservation")
        with self._lock:
            if self.stopped:
                self.rejected += 1
                raise BudgetExceeded(self.stop_reason or "budget_stopped")
            projected = self.spent + self.reserved + nbytes
            if projected > self.limit:
                self.stopped = True
                self.stop_reason = (f"budget_exceeded: projected {projected} > limit "
                                    f"{self.limit} (spent {self.spent}, reserved {self.reserved})")
                self.rejected += 1
                self.dump()
                raise BudgetExceeded(self.stop_reason)
            self.reserved += nbytes
            self.requests += 1
            return nbytes

    def commit(self, nbytes: int) -> None:
        """把预留转为已用；无论请求成功与否都按预留额计入（保守）。"""
        nbytes = int(nbytes)
        with self._lock:
            take = min(nbytes, self.reserved)
            self.reserved -= take
            self.spent += nbytes
            self.dump()

    def stop(self, reason: str) -> None:
        with self._lock:
            if not self.stopped:
                self.stopped = True
                self.stop_reason = reason
            self.dump()

    # -- 观测 --------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return {"limit": self.limit, "spent": self.spent,
                    "reserved": self.reserved, "requests": self.requests,
                    "rejected": self.rejected, "stopped": self.stopped,
                    "stop_reason": self.stop_reason, "started_at": self.started_at,
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    def dump(self) -> None:
        if not self.state_path:
            return
        data = self.snapshot()
        tmp = f"{self.state_path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, self.state_path)
        except OSError:
            # 状态文件写不进去不应改变预算判断（宁可继续按内存计数拦截）
            pass


def from_env() -> Budget | None:
    raw = os.environ.get("POOL_BUDGET_LIMIT")
    if not raw:
        return None
    return Budget(int(raw), os.environ.get("POOL_BUDGET_STATE"))
