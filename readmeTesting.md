# Testing — kato

From the repository root, install the project in your environment and run the unit test suite with:

```bash
pip install -e .
python3 -m unittest discover -s tests
```

The test suite includes:

- mocked unit tests for the orchestration services, especially `agent_service`, `implementation_service`, `repository_service`, and `testing_service`
- boundary tests for the provider clients and retry helpers
- small integration-style regressions that exercise the task-to-PR workflow shape without hitting live external systems

CI runs the same suite under `coverage` and prints a coverage summary in the job log.

If you only want to run a single test module, use:

```bash
python3 -m unittest discover -s tests -p 'test_notification_service.py'
```
