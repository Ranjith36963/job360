import json

d = json.load(open("backend_deploys.json", encoding="utf-8"))
print("backend top:", d[0]["status"], d[0]["meta"]["commitHash"][:7])

d2 = json.load(open("frontend_deploys.json", encoding="utf-8"))
print("frontend top:", d2[0]["status"], d2[0]["meta"]["commitHash"][:7])
for item in d2[:5]:
    print(item["status"], item["meta"]["commitHash"][:7], item["createdAt"])
