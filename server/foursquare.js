'use strict';

const fs = require('node:fs');
const path = require('node:path');

const API_BASE = 'https://places-api.foursquare.com';
const API_VERSION = '2025-06-17';
const DINING_CATEGORY_ID = '13000';
const SEARCH_RADIUS_METERS = 100;
const REQUEST_TIMEOUT_MS = 5000;

function normalizeText(value) {
  return String(value || '')
    .normalize('NFKC')
    .toLocaleLowerCase('ko-KR')
    .replace(/대한민국|south korea/gi, '')
    .replace(/[^0-9a-z가-힣]/g, '');
}

function normalizePhone(value) {
  return String(value || '').replace(/\D/g, '');
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

function addressesMatch(restaurant, candidate) {
  const kakao = [restaurant.roadAddress, restaurant.address]
    .map(normalizeText)
    .filter(Boolean);
  const fsq = [candidate.location?.address, candidate.location?.formatted_address]
    .map(normalizeText)
    .filter(Boolean);
  if (!kakao.length || !fsq.length) return false;
  return kakao.some(left => fsq.some(right => {
    if (left === right) return true;
    const shorter = left.length <= right.length ? left : right;
    const longer = left.length > right.length ? left : right;
    return shorter.length >= 6 && longer.includes(shorter);
  }));
}

function matchCandidate(restaurant, candidate) {
  const exactName = normalizeText(restaurant.name) !== ''
    && normalizeText(restaurant.name) === normalizeText(candidate.name);
  const addressMatch = addressesMatch(restaurant, candidate);
  const distance = distanceMeters(
    restaurant.latitude,
    restaurant.longitude,
    candidate.latitude,
    candidate.longitude
  );
  const sourcePhone = normalizePhone(restaurant.phone);
  const candidatePhone = normalizePhone(candidate.tel);
  const phoneMatch = sourcePhone.length >= 8 && sourcePhone === candidatePhone;

  // 실제 식당 사진으로 표시하므로 이름·주소·좌표가 모두 맞을 때만 후보로 인정한다.
  if (!exactName || !addressMatch || distance > 75) return null;
  const score = 100 + (distance <= 25 ? 20 : distance <= 50 ? 10 : 0) + (phoneMatch ? 15 : 0);
  return {
    candidate,
    score,
    evidence: {
      exactNormalizedName: true,
      addressMatch: true,
      distanceMeters: Math.round(distance),
      phoneMatch,
    },
  };
}

function selectUnambiguousMatch(restaurant, candidates) {
  const matches = (candidates || [])
    .map(candidate => matchCandidate(restaurant, candidate))
    .filter(Boolean)
    .sort((left, right) => right.score - left.score);
  if (!matches.length) return null;
  if (matches.length > 1 && matches[0].score - matches[1].score < 15) return null;
  return matches[0];
}

function photoId(photo) {
  return photo?.fsq_photo_id || photo?.id || '';
}

function photoURL(photo) {
  if (!photo?.prefix || !photo?.suffix) return null;
  try {
    const url = new URL(`${photo.prefix}800x600${photo.suffix}`);
    return url.protocol === 'https:' ? url.toString() : null;
  } catch {
    return null;
  }
}

function loadIds(storePath) {
  if (!storePath) return {};
  try {
    const parsed = JSON.parse(fs.readFileSync(storePath, 'utf8'));
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function saveIds(storePath, mappings) {
  if (!storePath) return;
  fs.mkdirSync(path.dirname(storePath), { recursive: true });
  const temporary = `${storePath}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(mappings, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(temporary, storePath);
}

function createFoursquareProvider({
  serviceKey = '',
  enabled = false,
  fetchImpl = globalThis.fetch,
  idStorePath = '',
} = {}) {
  const ids = loadIds(idStorePath);
  let blockedUntil = 0;

  async function requestJSON(url) {
    if (!enabled || !serviceKey || Date.now() < blockedUntil) return null;
    const response = await fetchImpl(url, {
      headers: {
        Authorization: `Bearer ${serviceKey}`,
        'X-Places-Api-Version': API_VERSION,
        Accept: 'application/json',
      },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (response.status === 429) {
      blockedUntil = Date.now() + 60_000;
      return null;
    }
    if (!response.ok) return null;
    return response.json();
  }

  async function searchMatch(restaurant) {
    const params = new URLSearchParams({
      query: restaurant.name,
      ll: `${restaurant.latitude},${restaurant.longitude}`,
      radius: String(SEARCH_RADIUS_METERS),
      fsq_category_ids: DINING_CATEGORY_ID,
      fields: 'fsq_place_id,name,latitude,longitude,location,tel,distance,categories',
      sort: 'DISTANCE',
      limit: '5',
    });
    const payload = await requestJSON(`${API_BASE}/places/search?${params}`);
    return selectUnambiguousMatch(restaurant, payload?.results || []);
  }

  async function fetchPhoto(placeId, preferredPhotoId) {
    const params = new URLSearchParams({
      limit: '10',
      sort: 'POPULAR',
      classifications: 'food_or_drink,indoor_or_ambience,outdoor_or_storefront',
    });
    const photos = await requestJSON(
      `${API_BASE}/places/${encodeURIComponent(placeId)}/photos?${params}`
    );
    if (!Array.isArray(photos)) return null;
    const selected = photos.find(photo => photoId(photo) === preferredPhotoId) || photos[0];
    const url = photoURL(selected);
    const id = photoId(selected);
    return url && id ? { id, url } : null;
  }

  async function enrich(restaurant) {
    if (!enabled || !serviceKey) return restaurant;
    try {
      const stored = ids[restaurant.id];
      let placeId = stored?.fsq_place_id;
      let evidence = placeId ? { previouslyVerifiedPlaceId: true } : null;
      if (!placeId) {
        const match = await searchMatch(restaurant);
        if (!match) return restaurant;
        placeId = match.candidate.fsq_place_id;
        evidence = match.evidence;
      }
      if (!placeId) return restaurant;

      const photo = await fetchPhoto(placeId, stored?.photo_id);
      if (!photo) return restaurant;
      if (!stored || stored.fsq_place_id !== placeId || stored.photo_id !== photo.id) {
        // PAYG에서 영구 저장이 허용된 Foursquare place/photo ID만 보관한다.
        ids[restaurant.id] = { fsq_place_id: placeId, photo_id: photo.id };
        saveIds(idStorePath, ids);
      }
      return {
        ...restaurant,
        photoURL: photo.url,
        photoKind: 'restaurantVerified',
        photoProvider: 'foursquare',
        photoSourceURL: `https://foursquare.com/v/${encodeURIComponent(placeId)}`,
        photoAttribution: 'Powered by Foursquare',
        photoMatchEvidence: evidence,
      };
    } catch {
      return restaurant;
    }
  }

  async function enrichRestaurants(restaurants) {
    if (!enabled || !serviceKey) return restaurants;
    const output = new Array(restaurants.length);
    let cursor = 0;
    async function worker() {
      while (cursor < restaurants.length) {
        const index = cursor;
        cursor += 1;
        output[index] = await enrich(restaurants[index]);
      }
    }
    await Promise.all(Array.from({ length: Math.min(4, restaurants.length) }, worker));
    return output;
  }

  return { enrichRestaurants };
}

module.exports = {
  addressesMatch,
  createFoursquareProvider,
  distanceMeters,
  normalizeText,
  photoURL,
  selectUnambiguousMatch,
};
