/**
 * Recurrence-expansion tests for js/timeline/util/time.js.
 *
 * Times are kept in UTC throughout the tests so DST doesn't change
 * results between summer and winter runs. (Production runs in local
 * time; the test harness pins both schedule and window in UTC.)
 */
import { test, describe } from 'node:test';
import assert from 'node:assert';
import { expandSchedule } from '../../../js/timeline/util/time.js';

// Helper: "2026-06-04T08:00:00Z" -> ms since epoch
const ms = (iso) => Date.parse(iso);

function S(overrides) {
  // Schedule with sensible defaults — override anything per-test.
  return {
    id: 'test-1',
    name: 'Test',
    playlistName: 'Pl',
    displayID: 'D',
    priority: 0,
    enabled: true,
    freq: 'DAILY',
    interval: 1,
    byweekday: [],
    dtstart: '2026-06-01',
    end: { type: 'never' },
    exdates: [],
    startTime: '08:00',
    endTime: '11:00',
    _serverVersion: 1,
    ...overrides,
  };
}

describe('expandSchedule — DAILY', () => {
  test('daily within a single day window yields one placement', () => {
    const s = S({ freq: 'DAILY' });
    const out = expandSchedule(s,
      ms('2026-06-04T00:00:00Z'),
      ms('2026-06-05T00:00:00Z'));
    assert.equal(out.length, 1);
    assert.equal(out[0].playlistName, 'Pl');
    assert.equal(out[0].displayID, 'D');
    assert.equal(out[0].scheduleId, 'test-1');
    assert.equal(out[0].startMs, ms('2026-06-04T08:00:00Z'));
    assert.equal(out[0].endMs,   ms('2026-06-04T11:00:00Z'));
  });

  test('daily across a 3-day window yields 3 placements', () => {
    const s = S({ freq: 'DAILY' });
    const out = expandSchedule(s,
      ms('2026-06-04T00:00:00Z'),
      ms('2026-06-07T00:00:00Z'));
    assert.equal(out.length, 3);
  });

  test('dtstart in the future yields zero placements', () => {
    const s = S({ freq: 'DAILY', dtstart: '2026-12-01' });
    const out = expandSchedule(s,
      ms('2026-06-04T00:00:00Z'),
      ms('2026-06-05T00:00:00Z'));
    assert.equal(out.length, 0);
  });

  test('interval=2 fires every other day', () => {
    const s = S({ freq: 'DAILY', interval: 2, dtstart: '2026-06-01' });
    const out = expandSchedule(s,
      ms('2026-06-01T00:00:00Z'),
      ms('2026-06-08T00:00:00Z'));
    // Days 1, 3, 5, 7 fire = 4 placements
    assert.equal(out.length, 4);
  });

  test('disabled schedule yields zero placements', () => {
    const s = S({ enabled: false });
    const out = expandSchedule(s,
      ms('2026-06-04T00:00:00Z'),
      ms('2026-06-05T00:00:00Z'));
    assert.equal(out.length, 0);
  });

  test('exdate skips that specific day', () => {
    const s = S({ exdates: ['2026-06-04'] });
    const out = expandSchedule(s,
      ms('2026-06-03T00:00:00Z'),
      ms('2026-06-06T00:00:00Z'));
    assert.equal(out.length, 2); // 3rd and 5th — 4th excluded
    for (const p of out) {
      assert.notEqual(new Date(p.startMs).getUTCDate(), 4);
    }
  });
});

describe('expandSchedule — WEEKLY', () => {
  test('byweekday=[0,1,2,3,4] (Mon-Fri) skips Sat+Sun', () => {
    const s = S({ freq: 'WEEKLY', byweekday: [0, 1, 2, 3, 4], dtstart: '2026-06-01' });
    // 2026-06-01 is a Monday. Window Mon-Sun.
    const out = expandSchedule(s,
      ms('2026-06-01T00:00:00Z'),
      ms('2026-06-08T00:00:00Z'));
    assert.equal(out.length, 5);
  });

  test('byweekday=[6] (Sun only) returns one placement in a week', () => {
    const s = S({ freq: 'WEEKLY', byweekday: [6], dtstart: '2026-06-01' });
    const out = expandSchedule(s,
      ms('2026-06-01T00:00:00Z'),
      ms('2026-06-08T00:00:00Z'));
    assert.equal(out.length, 1);
    // Should be the Sunday (2026-06-07)
    assert.equal(new Date(out[0].startMs).getUTCDay(), 0); // JS getUTCDay: 0=Sun
  });

  test('WEEKLY with empty byweekday fires once per week on dtstart DOW', () => {
    // REGRESSION GUARD: previously WEEKLY with byweekday=[] fired every
    // day in the window — diverged from iCal/dateutil semantics. Fix:
    // when byweekday is empty, default to dtstart's day-of-week (server
    // matches via dateutil.rrule). dtstart 2026-06-01 is a Monday → only
    // Monday placements within the week.
    const s = S({ freq: 'WEEKLY', byweekday: [], dtstart: '2026-06-01' });
    const out = expandSchedule(s,
      ms('2026-06-01T00:00:00Z'),
      ms('2026-06-15T00:00:00Z'));  // 2-week window
    assert.equal(out.length, 2);
    for (const p of out) {
      // jsDow: 0=Mon..6=Sun in our convention
      const dow = (new Date(p.startMs).getUTCDay() + 6) % 7;
      assert.equal(dow, 0, `expected Monday, got dow=${dow}`);
    }
  });
});

describe('expandSchedule — end={count}', () => {
  test('count=3 yields at most 3 placements', () => {
    const s = S({ freq: 'DAILY', end: { type: 'count', count: 3 } });
    const out = expandSchedule(s,
      ms('2026-06-01T00:00:00Z'),
      ms('2026-06-30T00:00:00Z'));
    assert.equal(out.length, 3);
  });

  test('count=3 with window AFTER dtstart yields zero (first 3 already past)', () => {
    // REGRESSION GUARD: previously the implementation iterated only
    // from windowStart-1, so the count quota was burned by candidate
    // days padded for cross-midnight rather than by actual occurrences.
    // The fix counts from dtstart per iCal RFC 5545 — the Nth fire is
    // the Nth fire regardless of which window the caller asks about.
    const s = S({ freq: 'DAILY', dtstart: '2026-06-01',
                  end: { type: 'count', count: 3 } });
    const out = expandSchedule(s,
      ms('2026-06-05T00:00:00Z'),
      ms('2026-06-30T00:00:00Z'));
    assert.equal(out.length, 0);
  });

  test('count=5 with one exdate yields 4 placements (exdate consumes quota)', () => {
    // iCal RFC 5545: COUNT counts the original occurrence set BEFORE
    // EXDATE filtering. count=5 + exdate-of-day-3 → 4 placements,
    // not 5. Matches dateutil.rrule on the server.
    const s = S({ freq: 'DAILY', dtstart: '2026-06-01',
                  exdates: ['2026-06-03'],
                  end: { type: 'count', count: 5 } });
    const out = expandSchedule(s,
      ms('2026-06-01T00:00:00Z'),
      ms('2026-06-30T00:00:00Z'));
    assert.equal(out.length, 4);
    const dates = out.map(p => {
      const d = new Date(p.startMs);
      return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
    });
    assert.deepEqual(dates,
      ['2026-06-01', '2026-06-02', '2026-06-04', '2026-06-05']);
  });
});

describe('expandSchedule — end={until}', () => {
  test('untilDate inclusive of last day', () => {
    const s = S({ freq: 'DAILY', dtstart: '2026-06-01',
                  end: { type: 'until', untilDate: '2026-06-03' } });
    const out = expandSchedule(s,
      ms('2026-06-01T00:00:00Z'),
      ms('2026-06-30T00:00:00Z'));
    assert.equal(out.length, 3); // 1, 2, 3
  });
});

describe('expandSchedule — cross-midnight (endTime <= startTime)', () => {
  test('22:00 → 02:00 fires from 22:00 of day to 02:00 next day', () => {
    const s = S({ freq: 'DAILY', startTime: '22:00', endTime: '02:00',
                  dtstart: '2026-06-04' });
    const out = expandSchedule(s,
      ms('2026-06-04T00:00:00Z'),
      ms('2026-06-05T12:00:00Z'));
    // We expect one placement starting 2026-06-04 22:00 and ending 2026-06-05 02:00
    assert.ok(out.length >= 1);
    const first = out[0];
    assert.equal(first.startMs, ms('2026-06-04T22:00:00Z'));
    assert.equal(first.endMs,   ms('2026-06-05T02:00:00Z'));
  });
});

describe('expandSchedule — clipping to window', () => {
  test('placement straddling window-start is clipped at window start', () => {
    const s = S({ freq: 'DAILY', startTime: '22:00', endTime: '02:00',
                  dtstart: '2026-06-01' });
    // Window starts mid-placement: 2026-06-05T00:00Z
    // The placement from 06-04 22:00 to 06-05 02:00 should appear, clipped
    // to start at the window-start.
    const winStart = ms('2026-06-05T00:00:00Z');
    const out = expandSchedule(s, winStart, ms('2026-06-05T12:00:00Z'));
    const cross = out.find(p => p.endMs === ms('2026-06-05T02:00:00Z'));
    assert.ok(cross, 'expected the cross-midnight placement to appear');
    assert.equal(cross.startMs, winStart, 'expected start clipped to window');
  });
});
