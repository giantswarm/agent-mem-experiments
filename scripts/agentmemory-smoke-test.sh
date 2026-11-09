#!/usr/bin/env bash
# Smoke test for a deployed agentmemory server.
#
#   AGENTMEMORY_URL=https://agentmemory.operations.awsprod.gigantic.io AGENTMEMORY_SECRET=... ./scripts/agentmemory-smoke-test.sh
#
# Writes two clearly labelled test memories (one shared to the team scope) and
# reads them back. Exit code 0 = every check passed.
set -euo pipefail

URL="${AGENTMEMORY_URL:?set AGENTMEMORY_URL}"
SECRET="${AGENTMEMORY_SECRET:?set AGENTMEMORY_SECRET}"
URL="${URL%/}"
RUN_ID="smoke-$(date +%s)"
fail=0

api() { # method path [json]
  local method=$1 path=$2 body=${3:-}
  if [ -n "$body" ]; then
    curl -fsS -m 30 -X "$method" -H "Authorization: Bearer $SECRET" \
      -H "Content-Type: application/json" -d "$body" "$URL$path"
  else
    curl -fsS -m 30 -X "$method" -H "Authorization: Bearer $SECRET" "$URL$path"
  fi
}
check() { # name condition-output
  if [ "$2" = "ok" ]; then echo "PASS  $1"; else echo "FAIL  $1: $2"; fail=1; fi
}
json() { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)"; }

# 1. Liveness without auth, protected endpoint rejects a missing token.
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$URL/agentmemory/livez")
check "livez reachable" "$([ "$code" = 200 ] && echo ok || echo "HTTP $code")"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$URL/agentmemory/memories")
check "unauthenticated read rejected" "$([ "$code" = 401 ] || [ "$code" = 403 ] && echo ok || echo "HTTP $code")"

# 2. State backend and providers as configured.
status=$(api GET /agentmemory/status)
echo "      status: $(echo "$status" | python3 -c 'import sys;print(sys.stdin.read()[:300].replace(chr(10)," "))')"

# 3. Write as two different users (agentId tags the writer).
mem_a=$(api POST /agentmemory/remember "{\"content\":\"[$RUN_ID] Team A convention: cluster apps are deployed with Flux from the MC repo\",\"type\":\"fact\",\"project\":\"agentmemory-smoke\",\"agentId\":\"user-a\"}" | json 'd["memory"]["id"]')
check "user A remember" "$([ -n "$mem_a" ] && echo ok || echo empty)"
mem_b=$(api POST /agentmemory/remember "{\"content\":\"[$RUN_ID] Team B convention: customer QBR decks live in the customer-success repo\",\"type\":\"fact\",\"project\":\"agentmemory-smoke\",\"agentId\":\"user-b\"}" | json 'd["memory"]["id"]')
check "user B remember" "$([ -n "$mem_b" ] && echo ok || echo empty)"

# 4. Share A's memory with the team and read the team feed.
shared_by=$(api POST /agentmemory/team/share "{\"itemId\":\"$mem_a\",\"itemType\":\"memory\",\"project\":\"agentmemory-smoke\"}" | json 'd["sharedItem"]["sharedBy"]')
check "team share (sharedBy=$shared_by)" "$([ -n "$shared_by" ] && echo ok || echo empty)"
in_feed=$(api GET "/agentmemory/team/feed?limit=50" | json "any('$RUN_ID' in str(i) for i in d['items'])")
check "team feed contains shared item" "$([ "$in_feed" = True ] && echo ok || echo "$in_feed")"

# 5. Recall through hybrid search.
hits=$(api POST /agentmemory/smart-search "{\"query\":\"$RUN_ID Flux management cluster repo\"}" | json "sum('$RUN_ID' in str(r) for r in d.get('results', []))")
check "smart-search finds the memory ($hits hits)" "$([ "${hits:-0}" -ge 1 ] && echo ok || echo "0 hits")"

# 6. MCP surface for clients.
tools=$(api GET /agentmemory/mcp/tools | json 'len(d.get("tools", d) if isinstance(d, dict) else d)')
check "MCP tools listed ($tools)" "$([ "${tools:-0}" -ge 8 ] && echo ok || echo "$tools")"

echo
[ $fail -eq 0 ] && echo "All checks passed (run id $RUN_ID)." || echo "Some checks failed (run id $RUN_ID)."
exit $fail
