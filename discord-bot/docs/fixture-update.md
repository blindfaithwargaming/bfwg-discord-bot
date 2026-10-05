# HTML Fixture Update Procedure

The files in `tests/fixtures/` are representative HTML used by parser tests. They mirror the real page structure closely enough to verify parsing logic, but PII, advertising, and unrelated scripts have been stripped.

## When to update

Update a fixture when:
- A parser test fails after a live site redesign.
- The live site adds a new field or changes a class name that the parser should consume.
- You want to extend test coverage for events with edge-case formatting.

Check the comment at the top of each fixture for the last verified date.

## Fixtures and their live sources

| Fixture file | Live URL | Parser |
|---|---|---|
| `tests/fixtures/cardiff_stadium_events.html` | `https://www.cardiffcitystadiumevents.co.uk/` | `bot/services/stadium_events.py` — `parse_cardiff_stadium_events` |
| `tests/fixtures/battlefields_events.html` | `https://www.thebattlefields.co.uk/events-at-the-battlefields?venue=0&category=22` | `bot/services/stadium_events.py` — `parse_battlefields_events` |

## Update procedure

1. **Fetch the live page** and save it locally:
   ```
   curl -o /tmp/live.html "https://www.cardiffcitystadiumevents.co.uk/"
   ```

2. **Run the parser against the live HTML** to confirm it still produces events:
   ```python
   from bot.services.stadium_events import parse_cardiff_stadium_events
   html = open("/tmp/live.html").read()
   events = parse_cardiff_stadium_events(html)
   print(events)
   ```
   If `events` is empty and the site has upcoming events, the page structure has changed and the parser needs updating.

3. **Strip the HTML** before committing. Remove or replace:
   - Scripts and tracking pixels (`<script>`, `<noscript>`, `<img>` analytics)
   - Ads, banners, and unrelated navigation
   - Personal names, email addresses, phone numbers
   - Session tokens, CSRF fields, or any user-specific markup
   - Keep only the structural elements the parser reads (event rows, titles, dates, links)

4. **Verify the stripped HTML still parses correctly:**
   ```
   cd discord-bot
   pytest tests/test_stadium_events.py -v
   ```

5. **Update the comment block** at the top of the fixture with today's date:
   ```html
   <!--
     Cardiff City Stadium events fixture
     Last verified against live site: YYYY-MM-DD
     Update procedure: see docs/fixture-update.md
   -->
   ```

6. **Commit with the date in the message**, for example:
   ```
   test: refresh cardiff_stadium_events fixture (2026-05-14)
   ```
