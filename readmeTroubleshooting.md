# Troubleshooting & Cost-Saving Tips — kato

## Troubleshooting

If something does not work as expected, the most common checks are:

1. Run `docker compose config` and confirm the rendered values match the working configuration.
2. Recreate the containers after changing `.env`.
3. Confirm the repository workspace is on the destination branch after a failure or after cleanup.
4. Check whether the active issue platform and repository provider are both configured in `.env`.
5. Verify that the OpenHands model credentials match the provider you selected.

Common failure modes:

- Bedrock authentication errors usually mean the AWS key, secret, region, or session token is wrong or stale.
- Branch-publish failures usually mean the task branch never got a committed change or the repo could not be restored cleanly.
- Dirty worktree errors mean the task branch still has uncommitted edits and the workspace needs cleanup before the next run.
- Missing git permissions usually mean the host repository path or SSH auth socket is not mounted the way the container expects.

## Saving Cost Tips

Use a cheaper main `OPENHANDS_LLM_MODEL`. This is usually the largest lever.
Lower `kato.retry.max_retries` from 3 to 2 or 3 if your setup is stable.
Keep `YOUTRACK_ISSUE_STATES` tight so only truly ready tasks get processed.
Batch review feedback into fewer comments, because each review-fix cycle can trigger more OpenHands work.
Keep task context lean: avoid huge pasted logs, long comment threads, and unnecessary attachments.
Keep task and review-comment handling lean so the in-memory workflow stays predictable during a run.
Don't expect much savings from poll interval tuning; that mostly affects waiting/API chatter, not LLM spend.
