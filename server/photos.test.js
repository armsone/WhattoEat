'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const { categoryQuery, createPhotoProvider, queryForRestaurant } = require('./photos');

function response(status, body) {
  return { status, ok: status >= 200 && status < 300, async json() { return body; } };
}

const restaurants = [1, 2].map(index => ({
  id: String(index),
  name: index === 1 ? '초월식당' : '다른식당',
  category: '음식점 > 한식',
  latitude: 37.385,
  longitude: 127.289,
}));

test('specific menu wins over broad Korean category when choosing a photo query', () => {
  assert.equal(categoryQuery('평양냉면집 음식점 > 한식'), 'naengmyeon korean cold noodles');
  assert.equal(categoryQuery('돈까스 음식점 > 일식'), 'tonkatsu pork cutlet');
  assert.equal(categoryQuery('음식점 > 한식 > 해물,생선'), 'seafood platter');
  assert.equal(categoryQuery('음식점 > 한식 > 육류,고기 > 삼겹살'), 'samgyeopsal korean pork belly');
  assert.equal(categoryQuery('음식점 > 한식 > 두부전문점'), 'korean tofu meal');
});

test('photo query uses restaurant name, then curated menu, then a concrete Chinese dish', () => {
  assert.equal(queryForRestaurant({
    name: '평양냉면 신흥관',
    curatedMenus: ['탕수육'],
    category: '음식점 > 중식 > 중국요리',
  }), 'naengmyeon korean cold noodles');
  assert.equal(queryForRestaurant({
    name: '신흥관',
    curatedMenus: ['탕수육'],
    category: '음식점 > 중식 > 중국요리',
  }), 'tangsuyuk Korean sweet sour pork');
  assert.match(queryForRestaurant({
    name: '신흥관',
    category: '음식점 > 중식 > 중국요리',
  }), /jajangmyeon|jjambbong|tangsuyuk/);
  assert.equal(queryForRestaurant({
    name: '평양순대국',
    category: '음식점 > 한식 > 순대,순댓국',
  }), 'sundaeguk Korean blood sausage soup');
});

test('Openverse assigns distinct, landscape, commercially reusable category examples', async () => {
  const results = [
    { id: 'mature', url: 'https://img.test/m.jpg', foreign_landing_url: 'https://source.test/m', width: 1200, height: 800, license: 'cc0', mature: true },
    { id: 'small', url: 'https://img.test/s.jpg', foreign_landing_url: 'https://source.test/s', width: 500, height: 400, license: 'by', mature: false },
    { id: 'bad-license', url: 'https://img.test/nc.jpg', foreign_landing_url: 'https://source.test/nc', width: 1200, height: 800, license: 'by-nc', mature: false },
    { id: 'one', url: 'https://img.test/1.jpg', foreign_landing_url: 'https://source.test/1', width: 1600, height: 900, license: 'cc0', creator: 'A', license_url: 'https://license.test/cc0' },
    { id: 'duplicate-url', url: 'https://img.test/1.jpg', foreign_landing_url: 'https://source.test/duplicate', width: 1600, height: 900, license: 'cc0' },
    { id: 'two', url: 'https://img.test/2.jpg', foreign_landing_url: 'https://source.test/2', width: 1400, height: 900, license: 'by', creator: 'B', license_url: 'https://license.test/by' },
  ];
  const provider = createPhotoProvider({ randomImpl: () => 0.999, fetchImpl: async url => {
    assert.match(String(url), /license=cc0%2Cpdm%2Cby/);
    assert.match(String(url), /mature=false/);
    return response(200, { results });
  } });
  const enriched = await provider.enrichRestaurants(restaurants);
  assert.deepEqual(enriched.map(item => item.photoId), ['one', 'two']);
  assert.deepEqual(enriched.map(item => item.photoKind), ['categoryExample', 'categoryExample']);
  assert.equal(enriched[0].photoCreator, 'A');
  assert.equal(enriched[1].photoLicense, 'by');
  assert.notEqual(enriched[0].photoURL, enriched[1].photoURL);
});

test('TourAPI exact normalized name within 75m takes precedence', async () => {
  const calls = [];
  const provider = createPhotoProvider({
    tourApiServiceKey: 'test-key',
    fetchImpl: async url => {
      calls.push(String(url));
      if (String(url).includes('searchKeyword1')) return response(200, { response: { body: { items: { item: [{
        contentid: 'tour-1', title: '초월 식당', mapy: 37.3851, mapx: 127.2891,
        firstimage: 'https://tour.test/restaurant.jpg', cpyrhtDivCd: 'Type1',
      }] } } } });
      return response(200, { results: [] });
    },
  });
  const [enriched] = await provider.enrichRestaurants([restaurants[0]]);
  assert.equal(enriched.photoKind, 'restaurantVerified');
  assert.equal(enriched.photoProvider, 'tourapi');
  assert.equal(enriched.photoURL, 'https://tour.test/restaurant.jpg');
  assert.equal(calls.some(url => url.includes('api.openverse.org')), false);
});

test('ambiguous or distant TourAPI match falls back, and photo failures preserve restaurants', async () => {
  const provider = createPhotoProvider({
    tourApiServiceKey: 'test-key',
    fetchImpl: async url => {
      if (String(url).includes('searchKeyword1')) return response(200, { response: { body: { items: { item: [{
        contentid: 'far', title: '초월식당', mapy: 38.0, mapx: 128.0,
        firstimage: 'https://tour.test/wrong.jpg',
      }] } } } });
      throw new Error('offline');
    },
  });
  assert.deepEqual(await provider.enrichRestaurants([restaurants[0]]), [restaurants[0]]);
});
