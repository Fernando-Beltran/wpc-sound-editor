import numpy as np

from wpcaudio.romscan import block_stats


def _pcm_sine(n=1024, period=64):
    t = np.arange(n)
    return (127 + 100 * np.sin(2 * np.pi * t / period)).astype(np.uint8).tobytes()


def test_pcm_like_block_has_high_adjacent_byte_correlation():
    assert block_stats(_pcm_sine()).autocorr > 0.9


def test_random_block_has_near_zero_adjacent_byte_correlation():
    noise = np.random.default_rng(0).integers(0, 256, 4096, dtype=np.uint8).tobytes()
    assert abs(block_stats(noise).autocorr) < 0.2


def test_constant_block_reports_full_fill_ratio():
    assert block_stats(b"\xff" * 512).fill_ratio == 1.0


def test_random_block_entropy_is_near_eight_bits():
    noise = np.random.default_rng(1).integers(0, 256, 16384, dtype=np.uint8).tobytes()
    assert block_stats(noise).entropy > 7.9


def test_constant_block_has_zero_entropy():
    assert block_stats(b"\x00" * 512).entropy == 0.0


def test_block_whose_lagged_half_is_constant_still_has_a_number():
    # Only the final byte differs, so bytes[:-1] has zero variance and a
    # naive Pearson correlation divides by zero and yields nan. A nan
    # silently fails every classification comparison, so a block like
    # this would be mislabelled rather than flagged.
    stats = block_stats(b"\x10" * 1023 + b"\x20")

    assert not np.isnan(stats.autocorr)
    assert stats.autocorr == 0.0
