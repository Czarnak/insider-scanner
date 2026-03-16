import os

with open("api_key.txt", "r") as f:
    key = f.read().strip()
    f.close()

os.environ["OPENSTATES_API_KEY"] = key

command = "python update_congress.py --include-state"
os.system(command)
