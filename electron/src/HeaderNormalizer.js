// @ts-check
/**
 * HeaderNormalizer
 * Normalizes Sec-CH-UA* and Accept-Language headers to match Chrome 138 fingerprint.
 */
const FULL_ACCEPT_LANG = 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7';
const CHROME_UA_HEADER = '"Chromium";v="138", "Google Chrome";v="138", "Not)A;Brand";v="99"';
const CHROME_UA_FULL_LIST = '"Chromium";v="138.0.0.0", "Google Chrome";v="138.0.0.0", "Not)A;Brand";v="99.0.0.0"';

function normalizeChromeHeaders(headers, url) {
  const h = { ...headers };
  for (const k of Object.keys(h)) {
    const lk = k.toLowerCase();
    if (lk === 'sec-ch-ua') h[k] = CHROME_UA_HEADER;
    else if (lk === 'sec-ch-ua-mobile') h[k] = '?0';
    else if (lk === 'sec-ch-ua-platform') h[k] = '"Windows"';
    else if (lk === 'sec-ch-ua-full-version-list') h[k] = CHROME_UA_FULL_LIST;
  }
  const alKey = Object.keys(h).find((k) => k.toLowerCase() === 'accept-language');
  if (alKey && (h[alKey] === 'ko' || h[alKey] === 'en' || !h[alKey])) h[alKey] = FULL_ACCEPT_LANG;
  return h;
}

function attachChromeHeaderNormalization(ses, label) {
  try {
    ses.webRequest.onBeforeSendHeaders((details, callback) => {
      callback({ requestHeaders: normalizeChromeHeaders(details.requestHeaders, details.url) });
    });
    console.log('[ChromeUA] Header normalization attached: ' + label);
  } catch (e) {
    console.warn('[ChromeUA] attach failed (' + label + '):', e && e.message);
  }
}

module.exports = {
  normalizeChromeHeaders,
  attachChromeHeaderNormalization,
};
