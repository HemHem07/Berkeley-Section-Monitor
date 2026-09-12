from courses import meeting_details
from desktop_backend import validate_event
import pytest


def test_meeting_details():
    first = '<div class="sf--meeting-details"><div class="sf--meeting-days">Mo, We</div><div class="sf--meeting-time"> 01:00 pm  -  01:59 pm </div><div class="sf--location"><a>Wheeler 202</a></div></div>'
    second = '<div class="sf--meeting-details"><div class="sf--meeting-days">Fr</div></div>'
    assert meeting_details(first + first + second) == 'Mo, We · 01:00 pm - 01:59 pm · Wheeler 202; Fr · Time TBD · Location TBD'
    assert meeting_details('<html></html>') == ''
    assert validate_event({'kind':'meeting', 'meeting':meeting_details(first)})
    with pytest.raises(ValueError):
        validate_event({'kind':'meeting', 'meeting':[]})
