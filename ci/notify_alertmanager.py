import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ALERTMANAGER_URL = os.environ.get(
    "ALERTMANAGER_URL",
    "http://monitoring-kube-prometheus-alertmanager.monitoring.svc:9093",
)
ALERT_ENDPOINT = ALERTMANAGER_URL.rstrip("/") + "/api/v2/alerts"

ALERT_NAME = "JenkinsPipelineFailed"
SERVICE_NAME = os.environ.get("SERVICE_NAME", "sre-lab-ci")
PIPELINE_TITLE = os.environ.get("PIPELINE_TITLE", "sre-lab CI")
LABELS = {
    "alertname": ALERT_NAME,
    "severity": "critical",
    "service": SERVICE_NAME,
    "namespace": "jenkins",
}
HOLD_OPEN_FOR = timedelta(hours=4)


def job_name():
    return os.environ.get("JOB_NAME", "sre-lab-ci")


def build_number():
    return os.environ.get("BUILD_NUMBER", "0")


def build_url():
    return os.environ.get("RUN_DISPLAY_URL") or os.environ.get("BUILD_URL", "")


def revision():
    short_sha = os.environ.get("SHORT_SHA", "").strip()
    if short_sha:
        return short_sha
    return os.environ.get("GIT_AFTER", "").strip()[:7] or "unknown"


def image_ref():
    return os.environ.get("IMAGE_REF", "").strip() or "本次未构建镜像"


def rfc3339(moment):
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def build_payload(mode):
    now = datetime.now(timezone.utc)
    firing = mode == "fire"
    labels = dict(LABELS)
    labels["job"] = job_name()
    return [{
        "labels": labels,
        "annotations": {
            "summary": "%s 流水线%s (build #%s)" % (
                PIPELINE_TITLE, "构建失败" if firing else "已恢复成功", build_number()),
            "description": "镜像=%s; 版本=%s; 触发=%s" % (
                image_ref(), revision(), build_url() or "未知"),
            "runbook_url": "sre-lab-local/03_runbook/04_发布与回滚手册.md",
        },
        "startsAt": rfc3339(now - HOLD_OPEN_FOR) if not firing else rfc3339(now),
        "endsAt": rfc3339(now + HOLD_OPEN_FOR) if firing else rfc3339(now),
        "generatorURL": build_url(),
    }]


def deliver(payload):
    request = urllib.request.Request(
        ALERT_ENDPOINT, data=json.dumps(payload).encode(), method="POST")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.status


def main(argv):
    if len(argv) != 2 or argv[1] not in ("fire", "resolve"):
        print("用法: notify_alertmanager.py fire|resolve")
        return 2
    payload = build_payload(argv[1])
    try:
        status = deliver(payload)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:200]
        print("告警投递被 Alertmanager 拒绝: HTTP %s %s" % (error.code, detail))
        return 1
    except Exception as error:
        print("告警投递失败(%s): %s" % (ALERT_ENDPOINT, error))
        return 1
    print("告警已投递: HTTP %s alertname=%s endsAt=%s endpoint=%s" % (
        status, ALERT_NAME, payload[0]["endsAt"], ALERT_ENDPOINT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
