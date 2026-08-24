'use strict';

// WhattoEat 참조 서버 — Node 20 표준 라이브러리만 사용 (http, fs, path, 내장 fetch).
// Kakao REST API 키는 KAKAO_REST_API_KEY 환경 변수로만 주입하며 응답/로그에 절대 노출하지 않는다.
// 사용자 좌표는 캐시하거나 로그로 남기지 않는다.

const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { createPhotoProvider } = require('./photos');

const PORT = Number(process.env.PORT) || 8080;
const KAKAO_KEY = process.env.KAKAO_REST_API_KEY || '';
const KAKAO_ENDPOINT = 'https://dapi.kakao.com/v2/local/search/category.json';
const photos = createPhotoProvider({
  tourApiServiceKey: process.env.TOUR_API_SERVICE_KEY || '',
});
const RADIUS_METERS = clamp(Number(process.env.SEARCH_RADIUS_METERS) || 1000, 100, 20000);
const PAGE_SIZE = 15;      // Kakao 최대 size
const MAX_PAGES = 4;       // 1~4페이지
const MAX_UNIQUE = 13;     // 화면 추천용 고유 place id 상한
const UPSTREAM_TIMEOUT_MS = 5000;

const DISCLAIMER =
  '카카오 로컬 API(카테고리 검색 FD6)는 메뉴, 판매 인기, 현재 영업 여부를 제공하지 않습니다. ' +
  '폐업·휴업 필터링은 적용되어 있지 않으므로 실제 이용 전 지도에서 확인이 필요합니다.';

// 운영자 확인 메뉴: Kakao place id → 메뉴 배열. 없어도 동작한다.
let curatedMenus = {};
try {
  curatedMenus = JSON.parse(
    fs.readFileSync(path.join(__dirname, 'curated-menus.json'), 'utf8')
  );
  console.log(`curated-menus.json loaded (${Object.keys(curatedMenus).length} entries)`);
} catch {
  console.log('curated-menus.json not found — curated menus disabled');
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function sendJSON(res, status, body) {
  const data = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  res.end(data);
}

function parseCoordinate(raw, min, max) {
  if (typeof raw !== 'string' || raw.trim() === '') return null;
  const value = Number(raw);
  if (!Number.isFinite(value) || value < min || value > max) return null;
  return value;
}

async function fetchKakaoPage(latitude, longitude, page) {
  const params = new URLSearchParams({
    category_group_code: 'FD6',
    x: String(longitude), // Kakao: x = 경도
    y: String(latitude),  // Kakao: y = 위도
    radius: String(RADIUS_METERS),
    sort: 'distance',
    size: String(PAGE_SIZE),
    page: String(page),
  });
  const response = await fetch(`${KAKAO_ENDPOINT}?${params}`, {
    headers: { Authorization: `KakaoAK ${KAKAO_KEY}` },
    signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
  });
  if (!response.ok) {
    const error = new Error(`kakao upstream status ${response.status}`);
    error.upstreamStatus = response.status;
    throw error;
  }
  return response.json();
}

function toRestaurant(doc) {
  return {
    id: doc.id,
    name: doc.place_name,
    category: doc.category_name || '',
    latitude: Number(doc.y),
    longitude: Number(doc.x),
    distanceMeters: doc.distance ? Number(doc.distance) : null,
    address: doc.address_name || null,
    roadAddress: doc.road_address_name || null,
    phone: doc.phone || null,
    placeURL: doc.place_url || null,
    // 현재 Kakao 카테고리 API는 영업 상태를 제공하지 않는다. 다른 제공자가 확인한 경우에만 bool로 채운다.
    isOpenNow: null,
    curatedMenus: Array.isArray(curatedMenus[doc.id]) ? curatedMenus[doc.id] : null,
  };
}

async function handleRestaurants(query, res) {
  const latitude = parseCoordinate(query.get('latitude'), -90, 90);
  const longitude = parseCoordinate(query.get('longitude'), -180, 180);
  if (latitude === null || longitude === null) {
    return sendJSON(res, 400, {
      error: 'invalid_coordinates',
      message: 'latitude(-90~90), longitude(-180~180) 쿼리 파라미터가 필요합니다.',
    });
  }
  if (!KAKAO_KEY) {
    return sendJSON(res, 503, {
      error: 'server_not_configured',
      message: '서버에 KAKAO_REST_API_KEY 환경 변수가 설정되어 있지 않습니다.',
    });
  }

  const byId = new Map();
  try {
    for (let page = 1; page <= MAX_PAGES; page += 1) {
      const result = await fetchKakaoPage(latitude, longitude, page);
      for (const doc of result.documents || []) {
        if (!byId.has(doc.id)) byId.set(doc.id, toRestaurant(doc));
        if (byId.size >= MAX_UNIQUE) break;
      }
      if (byId.size >= MAX_UNIQUE) break;
      if (!result.meta || result.meta.is_end) break;
    }
  } catch (error) {
    // 키·좌표 등 민감 정보는 로그/응답에 포함하지 않는다.
    if (error.name === 'TimeoutError' || error.name === 'AbortError') {
      console.error('upstream timeout');
      return sendJSON(res, 504, {
        error: 'upstream_timeout',
        message: '카카오 API 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.',
      });
    }
    console.error('upstream error:', error.upstreamStatus || error.name);
    return sendJSON(res, 502, {
      error: 'upstream_error',
      message: '카카오 API 호출에 실패했습니다. 잠시 후 다시 시도해 주세요.',
    });
  }

  let restaurants = [...byId.values()];
  try {
    restaurants = await photos.enrichRestaurants(restaurants);
  } catch {
    // 사진 공급자가 실패해도 핵심 식당 검색 응답은 그대로 제공한다.
    console.error('photo enrichment unavailable');
  }
  return sendJSON(res, 200, {
    restaurants,
    source: 'kakao-local-category-FD6',
    disclaimer: DISCLAIMER,
  });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (req.method !== 'GET') {
    return sendJSON(res, 405, { error: 'method_not_allowed', message: 'GET만 지원합니다.' });
  }
  if (url.pathname === '/health') {
    return sendJSON(res, 200, { status: 'ok' });
  }
  if (url.pathname === '/api/restaurants') {
    try {
      return await handleRestaurants(url.searchParams, res);
    } catch (error) {
      console.error('unexpected error:', error.name);
      return sendJSON(res, 500, {
        error: 'internal_error',
        message: '서버 내부 오류가 발생했습니다.',
      });
    }
  }
  return sendJSON(res, 404, { error: 'not_found', message: '지원하지 않는 경로입니다.' });
});

server.listen(PORT, () => {
  console.log(`WhattoEat server listening on port ${PORT}`);
  if (!KAKAO_KEY) {
    console.warn('WARNING: KAKAO_REST_API_KEY is not set — /api/restaurants will return 503');
  }
});
