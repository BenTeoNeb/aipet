"""`uv run python show.py file.json [from] [to]`: each case's context, as a model would see it."""

import json
import sys

sys.path.insert(0, sys.argv[0].rsplit("/", 1)[0])
from dec_lib import context

cases = json.load(open(sys.argv[1]))
a = int(sys.argv[2]) if len(sys.argv) > 2 else 0
b = int(sys.argv[3]) if len(sys.argv) > 3 else len(cases)
for i in range(a, min(b, len(cases))):
    print(f"=== [{i}] {cases[i]['title']}")
    print(json.dumps(context(cases[i]), ensure_ascii=False, indent=1))
