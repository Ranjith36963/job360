import json, sys

sha_prefix = "6422e46"

def check(path):
    try:
        d = json.load(open(path))
    except Exception as e:
        return "ERR:" + str(e)
    for item in d:
        ch = item.get("meta", {}).get("commitHash", "") or ""
        if ch.startswith(sha_prefix):
            return (item.get("status") or "") + "|" + ch
    return "NONE"

print("backend=" + check("/tmp/backend_deploys.json"))
print("frontend=" + check("/tmp/frontend_deploys.json"))
