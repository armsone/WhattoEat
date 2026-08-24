'use strict';

const OPENVERSE_ENDPOINT = 'https://api.openverse.org/v1/images/';
const TOUR_ENDPOINT = 'https://apis.data.go.kr/B551011/KorService1';
const MIN_WIDTH = 900;
const MIN_HEIGHT = 600;
const ALLOWED_LICENSES = new Set(['cc0', 'pdm', 'by']);
// iOS MenuPolicy.whitelist와 같은 순서다. 첫 대표 메뉴와 사진 검색어가 어긋나지 않게 함께 유지한다.
const MENU_QUERIES = [
  { variants: ['김밥'], query: 'gimbap korean rice roll' },
  { variants: ['냉면'], query: 'naengmyeon korean cold noodles' },
  { variants: ['돈가스', '돈까스'], query: 'tonkatsu pork cutlet' },
  { variants: ['초밥'], query: 'sushi japanese food' },
  { variants: ['국밥'], query: 'gukbap korean soup rice' },
  { variants: ['평양순대국', '순대국', '순댓국', '순대'], query: 'sundaeguk Korean blood sausage soup' },
  { variants: ['설렁탕'], query: 'seolleongtang korean ox bone soup' },
  { variants: ['칼국수'], query: 'kalguksu korean noodle soup' },
  { variants: ['햄버거'], query: 'hamburger meal' },
  { variants: ['피자'], query: 'pizza meal' },
  { variants: ['치킨'], query: 'korean fried chicken' },
  { variants: ['떡볶이'], query: 'tteokbokki korean spicy rice cake' },
  { variants: ['샤브샤브'], query: 'shabu shabu hot pot' },
  { variants: ['갈비탕'], query: 'galbitang korean beef soup' },
  { variants: ['짜장면', '자장면'], query: 'jajangmyeon black bean noodles' },
  { variants: ['짬뽕'], query: 'jjambbong Korean spicy seafood noodles' },
  { variants: ['탕수육'], query: 'tangsuyuk Korean sweet sour pork' },
  { variants: ['쌀국수'], query: 'vietnamese pho noodles' },
  { variants: ['마라탕'], query: 'malatang spicy hot pot' },
  { variants: ['파스타'], query: 'pasta meal' },
  { variants: ['곱창'], query: 'gopchang korean grilled intestine' },
  { variants: ['삼계탕'], query: 'samgyetang korean chicken soup' },
  { variants: ['보쌈'], query: 'bossam korean pork wraps' },
];

function normalizeText(value) {
  return String(value || '')
    .normalize('NFKC')
    .toLocaleLowerCase('ko-KR')
    .replace(/\([^)]*\)|\[[^\]]*\]/g, '')
    .replace(/본점|직영점/g, '')
    .replace(/[^0-9a-z가-힣]/g, '');
}

function distanceMeters(aLat, aLon, bLat, bLon) {
  const values = [aLat, aLon, bLat, bLon].map(Number);
  if (!values.every(Number.isFinite)) return Infinity;
  const [lat1, lon1, lat2, lon2] = values.map(value => value * Math.PI / 180);
  const dLat = lat2 - lat1;
  const dLon = lon2 - lon1;
  const h = Math.sin(dLat / 2) ** 2
    + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 6371000 * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
}

function categoryQuery(category) {
  const value = String(category || '');
  const menuMatch = MENU_QUERIES.find(entry =>
    entry.variants.some(variant => value.includes(variant))
  );
  if (menuMatch) return menuMatch.query;
  const mappings = [
    [/비빔국수|국수/, 'bibim guksu korean noodles'],
    [/삼겹살/, 'samgyeopsal korean pork belly'],
    [/찜닭|닭요리/, 'jjimdak korean braised chicken'],
    [/두부/, 'korean tofu meal'],
    [/오뎅|어묵/, 'korean fish cake eomuk'],
    [/해물|생선|회|수산/, 'seafood platter'],
    [/고기|육류|갈비/, 'korean barbecue meat'],
    [/한식뷔페|뷔페/, 'korean buffet food'],
    [/치킨|닭/, 'chicken food meal'],
    [/카페|커피|디저트|베이커리/, 'cafe dessert food'],
    [/아시아|베트남|태국|인도/, 'asian food meal'],
    [/일식|우동|라멘/, 'japanese food meal'],
    [/중식|중국|짬뽕/, 'chinese food meal'],
    [/양식|스테이크/, 'western food meal'],
    [/분식/, 'korean street food'],
    [/한식|찌개|탕|구이|족발/, 'korean food meal'],
  ];
  return mappings.find(([pattern]) => pattern.test(value))?.[1] || 'restaurant food meal';
}

function queryForRestaurant(restaurant) {
  // 상호에 음식명이 있으면 가장 강한 단서다. 운영자 대표 메뉴보다 먼저 적용한다.
  const name = String(restaurant.name || '');
  const nameMatch = MENU_QUERIES.find(entry =>
    entry.variants.some(variant => name.includes(variant))
  );
  if (nameMatch) return nameMatch.query;

  const curatedMenu = Array.isArray(restaurant.curatedMenus) ? restaurant.curatedMenus[0] : null;
  if (curatedMenu) {
    const known = MENU_QUERIES.find(entry =>
      entry.variants.some(variant => String(curatedMenu).includes(variant))
    );
    return known?.query || `${curatedMenu} food`;
  }
  const lastCategory = String(restaurant.category || '').split('>').at(-1)?.trim() || '';
  const haystacks = [lastCategory];
  const inferred = MENU_QUERIES.find(entry =>
    entry.variants.some(variant => haystacks.some(value => value.includes(variant)))
  );
  if (inferred) return inferred.query;

  // 메뉴가 따로 없는 중국집은 애매한 반찬 사진 대신 대표적인 한 접시로 제한한다.
  if (/중식|중국|중화/.test(String(restaurant.category || ''))) {
    const chineseQueries = [
      'jajangmyeon black bean noodles',
      'jjambbong Korean spicy seafood noodles',
      'tangsuyuk Korean sweet sour pork',
    ];
    const seed = [...name].reduce((sum, character) => sum + character.codePointAt(0), 0);
    return chineseQueries[seed % chineseQueries.length];
  }
  return categoryQuery(restaurant.category);
}

function broadCategoryQuery(category) {
  const value = String(category || '');
  if (/일식|우동|라멘/.test(value)) return 'japanese food meal';
  if (/중식|중국|짬뽕/.test(value)) return 'chinese food meal';
  if (/양식|스테이크/.test(value)) return 'western food meal';
  if (/카페|커피|디저트|베이커리/.test(value)) return 'cafe dessert food';
  if (/아시아|베트남|태국|인도/.test(value)) return 'asian food meal';
  if (/한식|분식|뷔페/.test(value)) return 'korean food meal';
  if (/술집|오뎅|어묵/.test(value)) return 'korean street food';
  return 'restaurant food meal';
}

function titlePatternForQuery(query) {
  const patterns = [
    [/naengmyeon/, /naengmyeon|naengmyun|cold noodle/i],
    [/gimbap/, /gimbap|kimbap|김밥/i],
    [/tonkatsu/, /tonkatsu|pork cutlet|돈가스|돈까스/i],
    [/sushi/, /sushi|초밥/i],
    [/gukbap/, /gukbap|soup rice|국밥/i],
    [/sundaeguk/, /sundaeguk|soondae|blood sausage soup|순대/i],
    [/seolleongtang/, /seolleongtang|ox bone soup|설렁탕/i],
    [/kalguksu/, /kalguksu|knife cut noodle|칼국수/i],
    [/tteokbokki/, /tteokbokki|rice cake|떡볶이/i],
    [/jajangmyeon/, /jajang|black bean noodle|짜장|자장/i],
    [/jjambbong/, /jjambbong|jjamppong|spicy seafood noodle|짬뽕/i],
    [/tangsuyuk/, /tangsuyuk|sweet sour pork|탕수육/i],
    [/samgyeopsal/, /samgyeopsal|pork belly|삼겹살/i],
    [/jjimdak/, /jjimdak|braised chicken|찜닭/i],
    [/tofu/, /tofu|dubu|두부/i],
    [/fish cake|eomuk/, /fish cake|eomuk|odeng|어묵|오뎅/i],
    [/seafood platter/, /seafood|fish|oyster|shrimp|prawn|crab|lobster|squid|octopus|clam|shellfish/i],
    [/barbecue meat/, /barbecue|bbq|meat|beef|pork|steak|갈비|고기/i],
    [/bibim guksu/, /bibim|guksu|korean noodle|국수/i],
  ];
  return patterns.find(([marker]) => marker.test(query))?.[1] || null;
}

function itemList(payload) {
  const items = payload?.response?.body?.items?.item;
  if (Array.isArray(items)) return items;
  return items ? [items] : [];
}

function safeHttpsURL(value) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'https:' ? parsed.toString() : null;
  } catch {
    return null;
  }
}

function decodedServiceKey(value) {
  try { return decodeURIComponent(String(value || '')); }
  catch { return String(value || ''); }
}

function openversePhoto(item) {
  const id = String(item?.id || '');
  const url = safeHttpsURL(item?.url) || safeHttpsURL(item?.thumbnail);
  const sourceURL = safeHttpsURL(item?.foreign_landing_url);
  const width = Number(item?.width);
  const height = Number(item?.height);
  const license = String(item?.license || '').toLowerCase();
  if (!id || !url || !sourceURL || item?.mature === true) return null;
  if (!ALLOWED_LICENSES.has(license)) return null;
  if (license === 'by' && (!item.creator || !safeHttpsURL(item.license_url))) return null;
  if (!Number.isFinite(width) || !Number.isFinite(height)
      || width < MIN_WIDTH || height < MIN_HEIGHT || width <= height) return null;
  return {
    id,
    url,
    metadata: {
      photoURL: url,
      photoKind: 'categoryExample',
      photoProvider: 'openverse',
      photoSourceURL: sourceURL,
      photoAttribution: `${item.creator ? `사진: ${item.creator} · ` : ''}${license.toUpperCase()} · Openverse`,
      photoCreator: item.creator || null,
      photoCreatorURL: safeHttpsURL(item.creator_url),
      photoLicense: license,
      photoLicenseURL: safeHttpsURL(item.license_url),
      photoTitle: item.title || null,
      photoId: id,
    },
  };
}

function createPhotoProvider({
  fetchImpl = globalThis.fetch,
  tourApiServiceKey = '',
  timeoutMs = 5000,
  randomImpl = Math.random,
} = {}) {
  const openverseCache = new Map();

  async function requestJSON(url) {
    const response = await fetchImpl(url, { signal: AbortSignal.timeout(timeoutMs) });
    if (!response.ok) return null;
    return response.json();
  }

  async function findTourPhoto(restaurant) {
    if (!tourApiServiceKey) return null;
    const params = new URLSearchParams({
      serviceKey: decodedServiceKey(tourApiServiceKey),
      MobileOS: 'ETC',
      MobileApp: 'WhattoEat',
      _type: 'json',
      pageNo: '1',
      numOfRows: '10',
      contentTypeId: '39',
      keyword: restaurant.name,
    });
    const payload = await requestJSON(`${TOUR_ENDPOINT}/searchKeyword1?${params}`);
    const exact = itemList(payload).filter(item =>
      normalizeText(item.title) === normalizeText(restaurant.name)
      && distanceMeters(restaurant.latitude, restaurant.longitude, item.mapy, item.mapx) <= 75
    );
    if (exact.length !== 1) return null;
    const match = exact[0];
    let imageURL = null;
    let sourceURL = null;
    if (match.cpyrhtDivCd === 'Type1') {
      imageURL = safeHttpsURL(match.firstimage || match.firstimage2);
      sourceURL = imageURL;
    }
    if (!imageURL && match.contentid) {
      const imageParams = new URLSearchParams({
        serviceKey: decodedServiceKey(tourApiServiceKey),
        MobileOS: 'ETC',
        MobileApp: 'WhattoEat',
        _type: 'json',
        contentId: String(match.contentid),
        imageYN: 'Y',
        subImageYN: 'Y',
      });
      const images = itemList(await requestJSON(`${TOUR_ENDPOINT}/detailImage1?${imageParams}`));
      const reusable = images.find(image => image.cpyrhtDivCd === 'Type1');
      imageURL = safeHttpsURL(reusable?.originimgurl || reusable?.smallimageurl);
      sourceURL = imageURL;
    }
    if (!imageURL) return null;
    return {
      photoURL: imageURL,
      photoKind: 'restaurantVerified',
      photoProvider: 'tourapi',
      photoSourceURL: sourceURL,
      photoAttribution: '한국관광공사 TourAPI',
      photoLicense: null,
      photoId: String(match.contentid || imageURL),
      photoMatchEvidence: {
        exactNormalizedName: true,
        distanceMeters: Math.round(distanceMeters(
          restaurant.latitude, restaurant.longitude, match.mapy, match.mapx
        )),
      },
    };
  }

  async function fetchOpenverse(query) {
    const cached = openverseCache.get(query);
    if (cached && Date.now() - cached.savedAt < 60 * 60 * 1000) {
      return shuffled(cached.photos);
    }
    const params = new URLSearchParams({
      q: query,
      license: [...ALLOWED_LICENSES].join(','),
      mature: 'false',
      aspect_ratio: 'wide',
      // 익명 API의 공식 상한은 20이다. 13개 화면에 필요한 수량을 한 번에 확보한다.
      page_size: '20',
    });
    const payload = await requestJSON(`${OPENVERSE_ENDPOINT}?${params}`);
    const titlePattern = titlePatternForQuery(query);
    const candidates = (payload?.results || [])
      .map(openversePhoto)
      .filter(Boolean)
      .filter(photo => !titlePattern || titlePattern.test(photo.metadata.photoTitle || ''));
    openverseCache.set(query, { photos: candidates, savedAt: Date.now() });
    return shuffled(candidates);
  }

  function shuffled(photos) {
    const candidates = [...photos];
    for (let index = candidates.length - 1; index > 0; index -= 1) {
      const swapIndex = Math.floor(randomImpl() * (index + 1));
      [candidates[index], candidates[swapIndex]] = [candidates[swapIndex], candidates[index]];
    }
    return candidates;
  }

  async function enrichRestaurants(restaurants) {
    if (!Array.isArray(restaurants) || restaurants.length === 0) return restaurants;
    const output = restaurants.map(restaurant => ({ ...restaurant }));

    if (tourApiServiceKey) {
      let cursor = 0;
      async function worker() {
        while (cursor < output.length) {
          const index = cursor++;
          try {
            const photo = await findTourPhoto(output[index]);
            if (photo) Object.assign(output[index], photo);
          } catch { /* 사진 오류는 식당 검색 결과를 제거하지 않는다. */ }
        }
      }
      await Promise.all(Array.from({ length: Math.min(4, output.length) }, worker));
    }

    const usedIds = new Set();
    const usedURLs = new Set();
    const missingQueries = [...new Set(output.filter(restaurant => !restaurant.photoURL)
      .map(queryForRestaurant))];
    const pools = new Map(await Promise.all(missingQueries.map(async query => {
      try { return [query, await fetchOpenverse(query)]; }
      catch { return [query, []]; }
    })));
    const fallbackPools = new Map();
    for (const restaurant of output) {
      if (restaurant.photoURL) {
        const id = String(restaurant.photoId || restaurant.photoURL);
        if (!usedIds.has(id) && !usedURLs.has(restaurant.photoURL)) {
          usedIds.add(id);
          usedURLs.add(restaurant.photoURL);
          continue;
        }
        for (const key of Object.keys(restaurant)) {
          if (key.startsWith('photo')) delete restaurant[key];
        }
      }
      const query = queryForRestaurant(restaurant);
      let photo = (pools.get(query) || []).find(candidate =>
        !usedIds.has(candidate.id) && !usedURLs.has(candidate.url)
      );
      if (!photo) {
        // 대표 메뉴 결과가 모자라도 모양이 가까운 음식까지만 완화한다.
        // 순대국은 일반 한식 사진이 아니라 국밥 사진으로, 중국 대표 메뉴는 다른 중국 대표 메뉴로 제한한다.
        const fallbackQuery = query.includes('sundaeguk')
          ? 'gukbap korean soup rice'
          : (/jajangmyeon|jjambbong|tangsuyuk/.test(query)
            ? 'jajangmyeon black bean noodles'
            : broadCategoryQuery(restaurant.category));
        if (!fallbackPools.has(fallbackQuery)) {
          try { fallbackPools.set(fallbackQuery, await fetchOpenverse(fallbackQuery)); }
          catch { fallbackPools.set(fallbackQuery, []); }
        }
        photo = fallbackPools.get(fallbackQuery).find(candidate =>
          !usedIds.has(candidate.id) && !usedURLs.has(candidate.url)
        );
      }
      if (!photo) continue;
      usedIds.add(photo.id);
      usedURLs.add(photo.url);
      Object.assign(restaurant, photo.metadata);
    }
    return output;
  }

  return { enrichRestaurants };
}

module.exports = {
  categoryQuery,
  createPhotoProvider,
  distanceMeters,
  normalizeText,
  openversePhoto,
  queryForRestaurant,
};
