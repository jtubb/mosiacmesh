import { test, describe } from 'node:test';
import assert from 'node:assert';
import { detectConflicts } from '../../../js/timeline/util/conflicts.js';

function P(over) {
  return {
    scheduleId: 'a',
    startMs: 0,
    endMs:   100,
    playlistName: 'Pl',
    displayID: 'D',
    priority: 0,
    ...over,
  };
}

describe('detectConflicts', () => {
  test('no overlap → empty', () => {
    const res = detectConflicts([
      P({ scheduleId: 'a', startMs: 0,   endMs: 50  }),
      P({ scheduleId: 'b', startMs: 100, endMs: 150 }),
    ]);
    assert.deepEqual(res, []);
  });

  test('full overlap → lower priority is loser, full-range stripe', () => {
    const res = detectConflicts([
      P({ scheduleId: 'a', priority: 0, startMs: 0,  endMs: 100 }),
      P({ scheduleId: 'b', priority: 5, startMs: 0,  endMs: 100 }),
    ]);
    assert.equal(res.length, 1);
    assert.equal(res[0].loserId, 'a');
    assert.equal(res[0].winnerId, 'b');
    assert.equal(res[0].overlapStartMs, 0);
    assert.equal(res[0].overlapEndMs, 100);
  });

  test('partial overlap → stripe over overlap region only', () => {
    const res = detectConflicts([
      P({ scheduleId: 'a', priority: 0, startMs: 0,  endMs: 60  }),
      P({ scheduleId: 'b', priority: 5, startMs: 30, endMs: 100 }),
    ]);
    assert.equal(res.length, 1);
    assert.equal(res[0].loserId, 'a');
    assert.equal(res[0].overlapStartMs, 30);
    assert.equal(res[0].overlapEndMs,   60);
  });

  test('equal priority → no conflict flagged (server tiebreaker is undefined; UI treats as parallel)', () => {
    const res = detectConflicts([
      P({ scheduleId: 'a', priority: 5, startMs: 0,  endMs: 100 }),
      P({ scheduleId: 'b', priority: 5, startMs: 0,  endMs: 100 }),
    ]);
    assert.deepEqual(res, []);
  });

  test('three-way overlap: lowest loses to both', () => {
    const res = detectConflicts([
      P({ scheduleId: 'a', priority: 0, startMs: 0,  endMs: 100 }),
      P({ scheduleId: 'b', priority: 5, startMs: 20, endMs: 60  }),
      P({ scheduleId: 'c', priority: 5, startMs: 70, endMs: 90  }),
    ]);
    // 'a' has two conflict entries — one for each overlap range
    const aRanges = res.filter(r => r.loserId === 'a');
    assert.equal(aRanges.length, 2);
  });
});
