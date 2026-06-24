"""Notification-dispatch logging (full-lifecycle logging gap I).

Every channel outcome — sent / queued / skipped / failed — now emits a
notification_dispatch line on job360.channels.dispatcher, so "why did/didn't a
send happen?" is answerable. (dispatch() calls _log_dispatch before every
return; here we verify the outcome mapping directly.)
"""
from __future__ import annotations

import logging

from src.services.channels.dispatcher import ChannelSendResult, _log_dispatch


def test_dispatch_logs_each_outcome(caplog):
    results = [
        ChannelSendResult(channel_id=1, channel_type="slack", ok=True),
        ChannelSendResult(channel_id=2, channel_type="email", ok=True, skipped=True, error="rule disabled"),
        ChannelSendResult(channel_id=3, channel_type="discord", ok=True, queued_digest=True, error="queued for bundle"),
        ChannelSendResult(channel_id=4, channel_type="webhook", ok=False, error="boom"),
    ]
    with caplog.at_level(logging.INFO, logger="job360.channels.dispatcher"):
        _log_dispatch("u1", 99, results)

    recs = [r for r in caplog.records if getattr(r, "event", "") == "notification_dispatch"]
    outcomes = {r.channel: r.outcome for r in recs}
    assert outcomes == {"slack": "sent", "email": "skipped", "discord": "queued", "webhook": "failed"}
    assert all(r.user_id == "u1" and r.job_id == 99 for r in recs)
