from wpcaudio.dac import decode_dac


def test_centre_byte_decodes_to_silence():
    assert decode_dac(b"\x80")[0] == 0


def test_extreme_bytes_span_most_of_the_int16_range():
    out = decode_dac(b"\x00\xff")
    assert out[0] < -32000
    assert out[1] > 32000


def test_one_sample_per_byte():
    assert len(decode_dac(b"\x40" * 10)) == 10
