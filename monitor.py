#!/usr/bin/env python3
import argparse
import html
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "data" / "state.json"
AVAILABILITY_URL = (
    "https://www.themodernlife.com/availability/apartment?"
    "availability=&bed=1&building=&floor=&order=ASC&param=avail"
)
AJAX_URL = "https://www.themodernlife.com/wp-content/themes/the-modern/floorplans-list-ajax.php"


def load_env(path):
    if not path or not Path(path).exists():
        return
    for raw_line in Path(path).read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config():
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"Missing config: {CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text())


def load_state():
    if not STATE_PATH.exists():
        return {"seen_fingerprints": []}
    return json.loads(STATE_PATH.read_text())


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def load_seen_units(state):
    seen = set(state.get("seen_units", []))
    for old_fingerprint in state.get("seen_fingerprints", []):
        unit = str(old_fingerprint).split("|", 1)[0].strip()
        if unit:
            seen.add(unit)
    return seen


def request_text(url, data=None):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    body = None
    if data is not None:
        body = urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(url, data=body, headers=headers)
    with urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def attr(block, name):
    match = re.search(rf'{re.escape(name)}="([^"]*)"', block)
    return html.unescape(match.group(1)).strip() if match else ""


def strip_tags(value):
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_date(value):
    value = (value or "").strip().lower()
    if not value or value == "now":
        return date.today()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def parse_floorplans(page_html):
    units = []
    for block in page_html.split('class="flrpln_list_content"')[1:]:
        block = 'class="flrpln_list_content"' + block
        row_match = re.search(r'<div class="flrpln_list_row">(.*?)</div>', block, re.S)
        row_html = row_match.group(1) if row_match else ""
        cols = [
            strip_tags(col)
            for col in re.findall(r'<span[^>]*class="flrpln_list_col[^"]*"[^>]*>(.*?)</span>', row_html, re.S)
        ]
        apply_match = re.search(r'<a[^>]+data-layer-event-apply-now[^>]+href="([^"]+)"', block, re.S)
        map_match = re.search(r'href="https://www\.themodernlife\.com/sightmap/\?unit=([^"]+)"', block)
        unit = attr(block, "data-unit") or (html.unescape(map_match.group(1)) if map_match else "")
        floor_plan = attr(block, "data-fp-name")
        price = attr(block, "data-monthly-price") or attr(block, "data-price")
        sqft = attr(block, "data-area")
        available = attr(block, "data-available")

        if len(cols) >= 7:
            building, bed_bath, floor, row_sqft, total_monthly, base_rent, row_available = cols[:7]
            sqft = sqft or row_sqft
            price = price or total_monthly
            available = row_available.replace("Available ", "") or available
        else:
            building = bed_bath = floor = base_rent = ""

        if not unit:
            continue
        units.append(
            {
                "unit": unit,
                "building": building,
                "bed_bath": bed_bath,
                "floor": floor,
                "sqft": sqft,
                "price": price.replace(".0000", ""),
                "base_rent": base_rent,
                "available": available,
                "available_date": parse_date(available).isoformat() if parse_date(available) else "",
                "floor_plan": floor_plan,
                "apply_url": html.unescape(apply_match.group(1)) if apply_match else "",
                "source_url": AVAILABILITY_URL,
            }
        )
    return units


def fetch_all_units():
    first_page = request_text(AVAILABILITY_URL)
    total_match = re.search(r'id="total_page" value="(\d+)"', first_page)
    condition_match = re.search(r'id="condition" value="([^"]+)"', first_page)
    total_pages = int(total_match.group(1)) if total_match else 1
    condition = html.unescape(condition_match.group(1)) if condition_match else ""

    units = parse_floorplans(first_page)
    for page in range(2, total_pages + 1):
        if not condition:
            break
        ajax_html = request_text(AJAX_URL, {"page": str(page), "condition": condition})
        units.extend(parse_floorplans(ajax_html))

    unique = {}
    for unit in units:
        unique[unit["unit"]] = unit
    return list(unique.values())


def unit_matches(unit, config):
    target_bedrooms = config.get("target_bedrooms")
    if target_bedrooms is not None:
        bed_match = re.match(r"\s*(\d+(?:\.\d+)?)\s*/", unit["bed_bath"])
        if not bed_match or float(bed_match.group(1)) != float(target_bedrooms):
            return False
    elif unit["bed_bath"] != config["target_bed_bath"]:
        return False
    available = parse_date(unit["available"])
    if not available:
        return False

    start = datetime.strptime(config["target_start_date"], "%Y-%m-%d").date()
    end_raw = config.get("target_end_date")
    mode = config.get("availability_mode", "within_range")
    if mode == "on_or_after":
        return available >= start
    if mode == "on_or_before":
        return available <= start
    if end_raw:
        end = datetime.strptime(end_raw, "%Y-%m-%d").date()
        return start <= available <= end
    return available >= start


def fingerprint(unit):
    return "|".join([unit["unit"], unit["available"], unit["price"], unit["floor_plan"]])


def unit_key(unit):
    return unit["unit"]


def money(value):
    value = value.strip()
    if not value:
        return ""
    return value if value.startswith("$") else f"${value}"


def build_message(new_units, all_matches, config):
    target = f"{config.get('target_bedrooms')} bedroom" if config.get("target_bedrooms") is not None else config["target_bed_bath"]
    end = config.get("target_end_date") or "以后"
    lines = [
        f"目标：{config['target_start_date']} 到 {end}，{target}，需要 {config.get('needed_units', 2)} 套",
        f"这次新增匹配：{len(new_units)} 套；当前匹配总数：{len(all_matches)} 套",
        "",
    ]
    for unit in new_units:
        lines.extend(
            [
                f"## Unit {unit['unit']}",
                f"- 楼栋/楼层：{unit['building']} Park / {unit['floor']}",
                f"- 户型：{unit['floor_plan']}，{unit['sqft']} sq ft",
                f"- 租金：{money(unit['price'])}/mo",
                f"- 可入住：{unit['available']}",
                f"- 申请链接：{unit['apply_url'] or unit['source_url']}",
                "",
            ]
        )
    lines.append(f"[查看官网房源]({AVAILABILITY_URL})")
    return "\n".join(lines)


def post_json(url, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=20) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def post_form(url, payload):
    data = urlencode(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(req, timeout=20) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def push(title, content):
    provider = (os.environ.get("WECHAT_PROVIDER") or "pushplus").lower().strip()
    if provider == "pushplus":
        token = os.environ.get("PUSHPLUS_TOKEN", "")
        if not token:
            raise RuntimeError("Missing PUSHPLUS_TOKEN")
        payload = {
            "token": token,
            "title": title,
            "content": content,
            "template": os.environ.get("PUSHPLUS_TEMPLATE") or "markdown",
            "channel": os.environ.get("PUSHPLUS_CHANNEL") or "wechat",
        }
        status, body = post_json("https://www.pushplus.plus/send", payload)
        print(f"PushPlus response: HTTP {status} {body}")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"PushPlus returned non-JSON response: {body}") from exc
        if status != 200 or int(parsed.get("code", -1)) != 200:
            raise RuntimeError(f"PushPlus send failed: {body}")
        return status, body
    if provider == "serverchan":
        sendkey = os.environ.get("SERVERCHAN_SENDKEY", "")
        if not sendkey:
            raise RuntimeError("Missing SERVERCHAN_SENDKEY")
        status, body = post_form(f"https://sctapi.ftqq.com/{sendkey}.send", {"title": title, "desp": content})
        print(f"ServerChan response: HTTP {status} {body}")
        if status != 200:
            raise RuntimeError(f"ServerChan send failed: {body}")
        return status, body
    if provider == "wecom":
        webhook = os.environ.get("WECOM_WEBHOOK", "")
        if not webhook:
            raise RuntimeError("Missing WECOM_WEBHOOK")
        status, body = post_json(webhook, {"msgtype": "markdown", "markdown": {"content": f"**{title}**\n\n{content}"}})
        print(f"WeCom response: HTTP {status} {body}")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"WeCom returned non-JSON response: {body}") from exc
        if status != 200 or int(parsed.get("errcode", -1)) != 0:
            raise RuntimeError(f"WeCom send failed: {body}")
        return status, body
    raise RuntimeError(f"Unsupported WECHAT_PROVIDER: {provider}")


def run(args):
    load_env(ROOT / ".env")
    if args.env_file:
        load_env(args.env_file)

    config = load_config()
    if args.push_test:
        return push("The Modern 监控测试", "微信推送通道已连通。如果你看到这条，说明 GitHub Actions 到手机的推送链路是通的。")

    state = load_state()
    seen_units = load_seen_units(state)
    all_units = fetch_all_units()
    matches = [unit for unit in all_units if unit_matches(unit, config)]
    matches.sort(key=lambda item: (item["available_date"], item["price"], item["unit"]))
    current_units = {unit_key(unit) for unit in matches}

    first_run = not seen_units
    if first_run and not config.get("notify_on_first_run", True):
        new_units = []
    else:
        new_units = matches if args.force_notify else [unit for unit in matches if unit_key(unit) not in seen_units]

    print(f"Fetched {len(all_units)} one-bedroom units.")
    print(f"Matched {len(matches)} target units.")
    for unit in matches:
        print(f"- {unit['unit']} {unit['available']} {money(unit['price'])} {unit['floor_plan']}")

    if args.dry_run:
        return None

    changed = False
    if new_units:
        title = f"The Modern 新增 {len(new_units)} 套 7/15后 1BR"
        if len(matches) >= int(config.get("needed_units", 2)):
            title += "，已够两套"
        content = build_message(new_units, matches, config)
        if args.push:
            push(title, content)
        changed = True
        print(f"Notification prepared for {len(new_units)} new unit(s).")
    else:
        print("No new matching units.")

    updated_seen_units = sorted(seen_units | current_units)
    current_fingerprints = sorted(fingerprint(unit) for unit in matches)
    if updated_seen_units != sorted(seen_units) or current_fingerprints != state.get("last_seen_fingerprints", []):
        state["seen_units"] = updated_seen_units
        state["last_seen_fingerprints"] = current_fingerprints
        state.pop("seen_fingerprints", None)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        changed = True

    return changed


def main():
    parser = argparse.ArgumentParser(description="Monitor The Modern Fort Lee 1B1B availability.")
    parser.add_argument("--push", action="store_true", help="Send WeChat notification for new matches.")
    parser.add_argument("--push-test", action="store_true", help="Send a one-off test notification.")
    parser.add_argument("--force-notify", action="store_true", help="Notify all current matches even if they were already seen.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print matches without sending or saving state.")
    parser.add_argument("--env-file", default="", help="Optional .env file to load for local testing.")
    args = parser.parse_args()
    try:
        run(args)
    except Exception as exc:
        print(f"Monitor failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
