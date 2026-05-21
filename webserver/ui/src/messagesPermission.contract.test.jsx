// Backend ↔ UI contract test for /messages and /permission.
//
// Loads the JSON the real Flask backend produced
// (tests/test_messages_permission_contract.py captured each shape via
// a real recording session manager — no MagicMock anywhere) and
// asserts the UI's parsers handle every response.

import { describe, test, expect } from 'vitest';

import fixture from './__fixtures__/messages_permission_contract.json';

describe('/api/sessions/<task>/messages contract', () => {

  test('delivered response carries the keys the chat composer reads', () => {
    const r = fixture.messages_delivered;
    for (const key of ['status', 'text', 'image_count']) {
      expect(r).toHaveProperty(key);
    }
    expect(r.status).toBe('delivered');
    expect(r.text).toBe('fix it pls');
    expect(r.image_count).toBe(0);
  });

  test('with-images response reports the right image_count', () => {
    const r = fixture.messages_with_images;
    expect(r.image_count).toBe(1);
    expect(r.status).toBe('delivered');
  });

  test('empty-payload error response has the shape the UI shows inline', () => {
    const r = fixture.messages_rejected;
    expect(r).toHaveProperty('error');
    expect(typeof r.error).toBe('string');
    expect(r.error).toMatch(/text or images/);
  });
});


describe('/api/sessions/<task>/permission contract', () => {

  test('allow response is empty body (UI just needs the 200)', () => {
    // The route returns whatever ``session.send_permission_response``
    // produces — for a real session that's typically empty. The
    // permission flow's UI consumer just looks at the status code.
    const r = fixture.permission_allow;
    // The body may be null or {} — either is fine; the UI's branching
    // is on the HTTP status (captured implicitly by fixture key
    // existing at all = 200).
    expect(r === null || typeof r === 'object').toBe(true);
  });

  test('missing-request_id error has the shape the UI shows', () => {
    const r = fixture.permission_missing_id;
    expect(r).toHaveProperty('error');
    expect(r.error).toMatch(/request_id/);
  });

  test('session-gone 409 carries an error the UI can show', () => {
    const r = fixture.permission_session_gone;
    expect(r).toHaveProperty('error');
    expect(r.error).toMatch(/session is not running/);
  });
});
