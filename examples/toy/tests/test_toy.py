from toy import clamp


def test_clamp_holds_a_value_between_the_bounds() -> None:
    assert clamp(15, 0, 10) == 10
    assert clamp(-3, 0, 10) == 0
    assert clamp(4, 0, 10) == 4
