"""C_MIN harness ordering: both C_MIN values back-to-back inside a pair,
randomized group order, unique arm ids."""
from __future__ import annotations

import pathlib
import random
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))

import cmin_experiment as ce  # noqa: E402

CMINS = [98304, 131072]


def test_cmin_variants_run_back_to_back():
    order = ce.build_order(CMINS, ce.ARMS, 5, random.Random(7))
    assert len(order) == 5 * len(ce.ARMS) * 2
    for rep in range(5):
        block = [x for x in order if x[0] == rep]
        assert len(block) == len(ce.ARMS) * 2
        for i in range(0, len(block), 2):
            first, second = block[i], block[i + 1]
            assert (first[2], first[3]) == (second[2], second[3])  # same worker group
            assert {first[1], second[1]} == set(CMINS)             # both C_MIN values


def test_group_and_pair_order_are_randomized():
    a = ce.build_order(CMINS, ce.ARMS, 3, random.Random(1))
    b = ce.build_order(CMINS, ce.ARMS, 3, random.Random(2))
    assert a != b


def test_arm_ids_are_unique_within_run():
    order = ce.build_order(CMINS, ce.ARMS, 5, random.Random(3))
    ids = [ce.arm_id('run-x', rep, name, cmin) for rep, cmin, _w, name in order]
    assert len(ids) == len(set(ids))
    assert all(i.startswith('run-x:r') for i in ids)
