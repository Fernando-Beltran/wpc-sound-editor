"""Spec for the per-block loudness silhouette."""

import numpy as np

from wpcaudio.thumbs import BUCKETS, LEVELS, envelope


def test_a_silhouette_has_one_value_per_bucket():
    assert len(envelope("dac", bytes(4800))) == BUCKETS
    assert len(envelope("cvsd", bytes(4800), buckets=16)) == 16


def test_silence_is_flat():
    assert set(envelope("dac", bytes([0x80] * 4800))) == {0}


def test_an_empty_block_does_not_blow_up():
    assert envelope("dac", b"") == [0] * BUCKETS


def test_a_loud_half_reads_louder_than_a_quiet_half():
    loud = np.full(2400, 0xFF, dtype=np.uint8)
    quiet = np.full(2400, 0x84, dtype=np.uint8)
    shape = envelope("dac", bytes(np.concatenate([quiet, loud])))

    assert max(shape[:BUCKETS // 2]) < max(shape[BUCKETS // 2:])
    assert max(shape) == LEVELS


def test_cvsd_idle_padding_reads_as_quiet():
    # 0x55 is alternating bits: no coincidence at all, which is how a
    # CVSD stream sits at rest.
    assert set(envelope("cvsd", bytes([0x55] * 4800))) == {0}


def test_a_cvsd_run_of_equal_bits_reads_as_loud():
    idle = bytes([0x55] * 2400)
    driven = bytes([0xFF] * 2400)
    shape = envelope("cvsd", idle + driven)

    assert max(shape[:BUCKETS // 2]) == 0
    assert min(shape[BUCKETS // 2:]) == LEVELS


def test_values_stay_inside_the_level_range():
    rng = np.random.default_rng(0)
    for kind in ("dac", "cvsd"):
        shape = envelope(kind, rng.integers(0, 256, 6000, dtype=np.uint8).tobytes())
        assert all(0 <= value <= LEVELS for value in shape)
