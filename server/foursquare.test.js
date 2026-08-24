'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const {
  createFoursquareProvider,
  selectUnambiguousMatch,
} = require('./foursquare');

const restaurant = {
  id: 'kakao-1',
  name: '초월식당',
  category: '음식점 > 한식',
  latitude: 37.385,
  longitude: 127.289,
  address: '경기 광주시 초월읍 대쌍령리 1-2',
  roadAddress: '경기 광주시 초월읍 경충대로 123',
  phone: '031-123-4567',
};

function response(status, body) {
  return {
    status,
    ok: status >= 200 && status < 300,
    async json() { return body; },
  };
}

test('strict match requires the same normalized name, address, and nearby coordinate', () => {
  const match = selectUnambiguousMatch(restaurant, [{
    fsq_place_id: 'fsq-1',
    name: '초월 식당',
    latitude: 37.3851,
    longitude: 127.2891,
    tel: '0311234567',
    location: { address: '경충대로 123' },
  }]);
  assert.equal(match.candidate.fsq_place_id, 'fsq-1');
  assert.equal(match.evidence.exactNormalizedName, true);
  assert.equal(match.evidence.addressMatch, true);
  assert.equal(match.evidence.phoneMatch, true);
  assert.ok(match.evidence.distanceMeters < 75);
});

test('ambiguous candidates are rejected instead of attaching a wrong restaurant photo', () => {
  const candidates = [0.0001, -0.0001].map((offset, index) => ({
    fsq_place_id: `fsq-${index}`,
    name: '초월식당',
    latitude: restaurant.latitude + offset,
    longitude: restaurant.longitude,
    location: { address: '경충대로 123' },
  }));
  assert.equal(selectUnambiguousMatch(restaurant, candidates), null);
});

test('verified Foursquare photo is ephemeral and only place/photo IDs persist', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'whattoeat-fsq-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const storePath = path.join(directory, 'ids.json');
  const requests = [];
  const fetchImpl = async url => {
    requests.push(String(url));
    if (String(url).includes('/places/search?')) {
      return response(200, { results: [{
        fsq_place_id: 'fsq-verified',
        name: '초월식당',
        latitude: 37.3851,
        longitude: 127.2891,
        tel: '0311234567',
        location: { address: '경충대로 123' },
      }] });
    }
    return response(200, [{
      id: 'photo-verified',
      prefix: 'https://fastly.4sqi.net/img/general/',
      suffix: '/photo.jpg',
      width: 1600,
      height: 1200,
    }]);
  };
  const provider = createFoursquareProvider({
    serviceKey: 'test-key-not-a-real-secret',
    enabled: true,
    fetchImpl,
    idStorePath: storePath,
  });

  const [enriched] = await provider.enrichRestaurants([restaurant]);
  assert.equal(enriched.photoKind, 'restaurantVerified');
  assert.equal(enriched.photoProvider, 'foursquare');
  assert.equal(enriched.photoURL, 'https://fastly.4sqi.net/img/general/800x600/photo.jpg');
  assert.equal(enriched.photoAttribution, 'Powered by Foursquare');
  assert.equal(requests.length, 2);

  const persisted = JSON.parse(fs.readFileSync(storePath, 'utf8'));
  assert.deepEqual(persisted, {
    'kakao-1': { fsq_place_id: 'fsq-verified', photo_id: 'photo-verified' },
  });
  assert.equal(JSON.stringify(persisted).includes('fastly'), false);
  assert.equal(JSON.stringify(persisted).includes('prefix'), false);
  assert.equal(JSON.stringify(persisted).includes('suffix'), false);
});

test('missing key, disabled provider, or rate limit preserves the Kakao response', async () => {
  let calls = 0;
  const noKey = createFoursquareProvider({
    enabled: true,
    fetchImpl: async () => { calls += 1; return response(200, {}); },
  });
  assert.deepEqual(await noKey.enrichRestaurants([restaurant]), [restaurant]);

  const disabled = createFoursquareProvider({
    serviceKey: 'test-key',
    enabled: false,
    fetchImpl: async () => { calls += 1; return response(200, {}); },
  });
  assert.deepEqual(await disabled.enrichRestaurants([restaurant]), [restaurant]);

  const limited = createFoursquareProvider({
    serviceKey: 'test-key',
    enabled: true,
    fetchImpl: async () => { calls += 1; return response(429, {}); },
  });
  assert.deepEqual(await limited.enrichRestaurants([restaurant]), [restaurant]);
  assert.equal(calls, 1);
});
