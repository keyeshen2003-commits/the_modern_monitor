# The Modern Fort Lee Monitor

This monitors The Modern's official availability page for target apartment layouts available on or after July 15, 2026, then sends a WeChat-compatible push when a new matching unit appears.

Default target:

- Building: The Modern, Fort Lee
- Layout: 1 bedroom, plus 2 bedroom / 2 bath, including 2 bedroom / 2 bath + den
- Availability: on or after `2026-07-15`
- Needed units: `2`
- Check frequency: every 5 minutes on GitHub Actions

## Why Cloud

Phones are good notification receivers, but they are not reliable background web monitors, especially when locked. This setup runs in GitHub's cloud, so your computer can be off. Your phone only needs to receive the WeChat / PushPlus notification.

## Deploy From A Phone

1. Create a private GitHub repository.
2. Upload these files to the repository root.
3. In the repo, open `Settings > Secrets and variables > Actions > New repository secret`.
4. Add the push secrets you use:
   - `WECHAT_PROVIDER`: usually `pushplus`
   - `PUSHPLUS_TOKEN`: your PushPlus token
   - `PUSHPLUS_CHANNEL`: `wechat`
   - `PUSHPLUS_TEMPLATE`: `markdown`
5. Open `Actions > The Modern monitor > Run workflow` once to test.

After that, GitHub checks every 5 minutes and sends a message only when it sees a new matching apartment.

## Local Test

From this folder:

```bash
python3 monitor.py --dry-run
python3 monitor.py --push-test --env-file /Users/keyeshen/Desktop/codex/ibkr_daily_briefing/.env
python3 monitor.py --push --env-file /Users/keyeshen/Desktop/codex/ibkr_daily_briefing/.env
```

## Adjust The Target

Edit `config.json`.

- `target_layouts` controls accepted layouts. The current setup allows 1BR and 2BR / 2BA.
- `availability_mode: "within_range"` means only dates between `target_start_date` and `target_end_date`.
- `availability_mode: "on_or_after"` means anything available on or after `target_start_date`.
- `availability_mode: "on_or_before"` means anything available by `target_start_date`.

For your current request, `on_or_after` with `target_start_date: "2026-07-15"` keeps the date filter broad while watching the selected layouts.
