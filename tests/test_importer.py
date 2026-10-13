"""Spec for the other direction: a WAV turned into bytes that fit a block."""

import numpy as np
import pytest

from wpcaudio.cvsd import Hc55516Decoder, pack_bits, unpack_bits
from wpcaudio.dac import decode_dac, encode_dac
from wpcaudio.soundrom import DAC_SILENCE
from wpcaudio.importer import prepare, resample
from wpcaudio.soundrom import SoundRom
from wpcaudio.wavio import read_wav, write_wav

BIT_RATE = 22050
DAC_RATE = 11025


def _tone(freq, seconds, rate, amplitude=12000):
    t = np.arange(int(seconds * rate)) / rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.int16)


# -- the pieces ------------------------------------------------------


def test_a_wav_survives_a_write_and_a_read(tmp_path):
    original = _tone(440, 0.05, 8000)
    path = write_wav(tmp_path / "x.wav", original, 8000)

    samples, rate = read_wav(path)

    assert rate == 8000
    assert np.array_equal(samples, original)


def test_resampling_scales_the_length_by_the_rate_ratio():
    assert len(resample(np.zeros(1000), 22050, 11025)) == 500
    assert len(resample(np.zeros(500), 11025, 22050)) == 1000


def test_resampling_keeps_a_tone_at_its_own_frequency():
    tone = _tone(300, 0.5, 22050)
    out = resample(tone, 22050, 11025)

    spectrum = np.abs(np.fft.rfft(out * np.hanning(len(out))))
    peak = np.fft.rfftfreq(len(out), 1 / 11025)[spectrum.argmax()]
    assert abs(peak - 300) < 10, "el tono se fue a %.0f Hz" % peak


def test_dac_encoding_centres_silence_and_round_trips():
    assert encode_dac(np.zeros(4, dtype=np.int16)) == b"\x80\x80\x80\x80"
    assert encode_dac(np.array([32767, -32768], dtype=np.int16)) == b"\xff\x00"

    tone = _tone(300, 0.1, DAC_RATE)
    back = decode_dac(encode_dac(tone))
    assert np.corrcoef(tone.astype(float), back.astype(float))[0, 1] > 0.999


def test_packing_bits_is_the_inverse_of_unpacking_them():
    data = b"\x00\xff\x5a\xa5"
    assert pack_bits(unpack_bits(data)) == data


def test_packing_refuses_a_bit_count_that_is_not_whole_bytes():
    with pytest.raises(ValueError):
        pack_bits([1, 0, 1])


# -- preparing a replacement -----------------------------------------


def _rom_with_one_block_of(kind, size):
    """A ROM carrying a single block of `size` bytes of the given kind."""
    rom = SoundRom(u18=bytearray(0x80000), u15=bytearray(0x80000))

    def put(bank, addr, payload):
        chip, offset = rom.resolve(bank, addr)
        rom.chips[chip][offset:offset + len(payload)] = payload

    end = 0x6000 + size
    record = bytes([0x7C, 0x60, 0x00, end >> 8, end & 0xFF])
    if kind == "dac":
        put(0x7C, 0x4003, b"\x50\x00")
        put(0x7C, 0x5000, record)
        put(0x7C, 0x5005, bytes(5))
        put(0x7C, 0x4015, b"\x51\x00")
        put(0x7C, 0x5100, bytes(2))
    else:
        put(0x7C, 0x4003, b"\x50\x00")
        put(0x7C, 0x5000, bytes(5))
        put(0x7C, 0x4015, b"\x51\x00")
        put(0x7C, 0x5100, b"\x52\x00")
        put(0x7C, 0x5102, bytes(2))
        put(0x7C, 0x5200, record)
    return rom, next(b for b in rom.blocks() if b.kind == kind)


def test_a_dac_replacement_fills_the_block_and_decodes_back():
    rom, block = _rom_with_one_block_of("dac", 2048)
    tone = _tone(300, 2048 / DAC_RATE, DAC_RATE)

    data, report = prepare(block, tone, DAC_RATE, dac_rate=DAC_RATE)

    assert len(data) == block.size
    assert report.truncated == 0 and report.padded == 0
    back = decode_dac(data).astype(float)
    assert np.corrcoef(tone.astype(float), back)[0, 1] > 0.99


def test_a_cvsd_replacement_fills_the_block_and_decodes_back():
    rom, block = _rom_with_one_block_of("cvsd", 1024)
    seconds = block.size * 8 / BIT_RATE
    tone = _tone(300, seconds, BIT_RATE)

    data, report = prepare(block, tone, BIT_RATE, bit_rate=BIT_RATE)

    assert len(data) == block.size
    assert report.truncated == 0 and report.padded == 0
    back = Hc55516Decoder().decode(unpack_bits(data)).astype(float)
    assert np.corrcoef(tone.astype(float), back)[0, 1] > 0.9


def test_a_wav_at_another_rate_is_resampled_to_fit():
    rom, block = _rom_with_one_block_of("dac", 2048)
    # 2048 DAC samples at 11025 Hz is the same audio as this at 44100.
    tone = _tone(300, 2048 / DAC_RATE, 44100)

    data, report = prepare(block, tone, 44100, dac_rate=DAC_RATE)

    assert len(data) == block.size
    assert report.padded <= 1 and report.truncated <= 1


def test_too_much_audio_is_truncated_and_the_report_says_by_how_much():
    rom, block = _rom_with_one_block_of("dac", 1000)
    tone = _tone(300, 2000 / DAC_RATE, DAC_RATE)

    _, report = prepare(block, tone, DAC_RATE, dac_rate=DAC_RATE)

    assert report.truncated == 1000
    assert report.capacity_seconds == pytest.approx(1000 / DAC_RATE)
    # The source is twice the block, so half of it is what survives.
    assert report.source_seconds == pytest.approx(2000 / DAC_RATE)


def test_a_replacement_written_back_reads_out_unchanged():
    rom, block = _rom_with_one_block_of("dac", 2048)
    sizes = {name: len(image) for name, image in rom.chips.items()}
    tone = _tone(300, 2048 / DAC_RATE, DAC_RATE)

    data, _ = prepare(block, tone, DAC_RATE, dac_rate=DAC_RATE)
    rom.write(block, data)

    assert rom.extract(block) == data
    assert {name: len(image) for name, image in rom.chips.items()} == sizes


def test_saving_writes_every_image_back_out(tmp_path):
    rom, block = _rom_with_one_block_of("dac", 64)
    rom.write(block, b"\x55" * 64)

    paths = {name: tmp_path / ("%s.rom" % name) for name in rom.chips}
    rom.save(paths)

    reread = SoundRom(u18=paths["u18"].read_bytes(), u15=paths["u15"].read_bytes())
    assert reread.extract(block) == b"\x55" * 64


def test_wav_round_trips_through_memory_without_a_file():
    import io

    original = _tone(440, 0.02, 8000)
    buffer = io.BytesIO()
    write_wav(buffer, original, 8000)
    buffer.seek(0)

    samples, rate = read_wav(buffer)
    assert rate == 8000
    assert np.array_equal(samples, original)


# -- choosing which part of the audio survives ------------------------


def test_capacity_is_reported_in_samples_so_a_slider_has_a_range():
    rom, block = _rom_with_one_block_of("dac", 1000)
    _, report = prepare(block, _tone(300, 0.5, DAC_RATE), DAC_RATE, dac_rate=DAC_RATE)

    # Half a second at 11025 Hz against a 1000-sample block.
    assert report.capacity_samples == 1000
    assert report.source_samples == int(0.5 * DAC_RATE)
    assert report.max_offset == report.source_samples - 1000


def test_an_offset_picks_a_later_window_of_a_long_wav():
    rom, block = _rom_with_one_block_of("dac", 1000)
    ramp = np.linspace(-20000, 20000, 4000).astype(np.int16)

    start, _ = prepare(block, ramp, DAC_RATE, dac_rate=DAC_RATE, offset=0)
    later, _ = prepare(block, ramp, DAC_RATE, dac_rate=DAC_RATE, offset=3000)

    assert len(start) == len(later) == 1000
    assert decode_dac(later)[0] > decode_dac(start)[0], "no se movio la ventana"


def test_a_short_wav_can_be_pushed_later_in_the_block():
    rom, block = _rom_with_one_block_of("dac", 2000)
    tone = _tone(300, 500 / DAC_RATE, DAC_RATE)

    data, report = prepare(block, tone, DAC_RATE, dac_rate=DAC_RATE, offset=1000)

    assert report.max_offset == 1500
    # Silence first, then the audio where the offset put it.
    assert set(data[:1000]) == {DAC_SILENCE}
    assert set(data[1000:1500]) != {DAC_SILENCE}


def test_an_offset_past_the_end_is_clamped_not_an_error():
    rom, block = _rom_with_one_block_of("dac", 1000)
    tone = _tone(300, 0.5, DAC_RATE)

    _, report = prepare(block, tone, DAC_RATE, dac_rate=DAC_RATE, offset=10 ** 6)

    assert report.offset == report.max_offset


def test_cvsd_capacity_counts_eight_samples_per_byte():
    rom, block = _rom_with_one_block_of("cvsd", 1024)
    _, report = prepare(block, _tone(300, 0.1, BIT_RATE), BIT_RATE, bit_rate=BIT_RATE)

    assert report.capacity_samples == 1024 * 8


# -- making audio fit without cutting it ------------------------------


def test_a_short_wav_can_be_looped_to_fill_the_block():
    rom, block = _rom_with_one_block_of("dac", 2000)
    tone = _tone(300, 500 / DAC_RATE, DAC_RATE)

    data, report = prepare(block, tone, DAC_RATE, dac_rate=DAC_RATE, mode="loop")

    assert len(data) == block.size
    assert report.padded == 0, "un bucle no deja silencio"
    assert report.mode == "loop"
    # The second pass through is the same audio as the first.
    decoded = decode_dac(data)
    assert np.allclose(decoded[:500], decoded[500:1000], atol=600)


def test_speed_stretches_the_audio_to_the_exact_size():
    rom, block = _rom_with_one_block_of("dac", 2000)
    tone = _tone(300, 500 / DAC_RATE, DAC_RATE)

    data, report = prepare(block, tone, DAC_RATE, dac_rate=DAC_RATE, mode="speed")

    assert len(data) == block.size
    assert report.truncated == 0 and report.padded == 0
    assert report.speed == pytest.approx(500 / 2000, rel=0.01)


def test_speed_also_squeezes_audio_that_is_too_long():
    rom, block = _rom_with_one_block_of("dac", 1000)
    tone = _tone(300, 4000 / DAC_RATE, DAC_RATE)

    data, report = prepare(block, tone, DAC_RATE, dac_rate=DAC_RATE, mode="speed")

    assert len(data) == block.size
    assert report.speed == pytest.approx(4.0, rel=0.01)


def test_auto_nudges_the_speed_when_the_mismatch_is_small():
    rom, block = _rom_with_one_block_of("dac", 2000)
    # Ten per cent too long: close enough to fix by playing it faster.
    tone = _tone(300, 2200 / DAC_RATE, DAC_RATE)

    _, report = prepare(block, tone, DAC_RATE, dac_rate=DAC_RATE, mode="auto")

    assert report.mode == "speed"
    assert report.truncated == 0


def test_auto_trims_rather_than_distorting_a_much_longer_wav():
    rom, block = _rom_with_one_block_of("dac", 1000)
    tone = _tone(300, 8000 / DAC_RATE, DAC_RATE)

    _, report = prepare(block, tone, DAC_RATE, dac_rate=DAC_RATE, mode="auto")

    assert report.mode == "trim"
    assert report.truncated > 0


def test_auto_loops_rather_than_slowing_a_much_shorter_wav():
    rom, block = _rom_with_one_block_of("dac", 4000)
    tone = _tone(300, 500 / DAC_RATE, DAC_RATE)

    _, report = prepare(block, tone, DAC_RATE, dac_rate=DAC_RATE, mode="auto")

    assert report.mode == "loop"
    assert report.padded == 0


def test_an_unknown_mode_is_refused():
    rom, block = _rom_with_one_block_of("dac", 1000)
    with pytest.raises(ValueError):
        prepare(block, _tone(300, 0.1, DAC_RATE), DAC_RATE,
                dac_rate=DAC_RATE, mode="telepatia")
