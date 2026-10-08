# Vision TCP and Core communication contract

Core now uses the existing Vision status request for both device polling and Process Manager checks. The previous request to change Vision's accepted `params` is superseded. Do not edit the Vision repository for this issue.

## Ports and request

- Vision web UI: HTTP `8767`.
- Core-facing Vision server: JSON Lines TCP `8768`.
- Both Core paths send one UTF-8 JSON line followed by a newline:

```json
{"id":"core-request-id","command":"vision.status","params":{}}
```

The response must retain the top-level request `id`, `ok: true`, and a status object in `result`. A successful TCP response and `result.online: false` (camera offline) are different states.

## Linux deployment checks

1. Verify the deployed Core Vision TCP host and port point to the Vision server on `8768`, separate from web port `8767`.
2. Verify `vision/tools/start_fvp_basler_camera.sh` started a TCP listener on `8768`.
3. Check Core's Vision TCP Communication error: `INVALID_PARAMS` suggests an older Core request; `connect failed` or `timeout` suggests host, listener, or firewall trouble.
4. Opening the web UI alone does not verify TCP communication.

Live Linux device and camera communication could not be checked on this Windows workspace.
