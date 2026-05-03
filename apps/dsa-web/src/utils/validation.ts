interface ValidationResult {
  valid: boolean;
  message?: string;
  normalized: string;
}

interface BatchStockCodeParseResult {
  isBatch: boolean;
  codes: string[];
  invalidCodes: string[];
}

const SUPPORTED_QUERY_CHARACTERS = /^[A-Z0-9.\u3400-\u9FFF\s]+$/;

const STOCK_CODE_PATTERNS = [
  /^\d{6}$/, // A-share 6-digit code
  /^(SH|SZ|BJ)\d{6}$/, // A-share code with exchange prefix
  /^\d{6}\.(SH|SZ|SS|BJ)$/, // A-share code with exchange suffix
  /^\d{5}$/, // HK code without prefix
  /^HK\d{1,5}$/, // HK-prefixed code, for example HK00700
  /^\d{1,5}\.HK$/, // HK suffix format, for example 00700.HK
  /^[A-Z]{1,5}(?:\.(?:US|[A-Z]))?$/, // Common US ticker format
];

const BATCH_STOCK_CODE_SPLITTER = /[,，]/;

/**
 * Check whether the input looks like a stock code.
 */
export const looksLikeStockCode = (value: string): boolean => {
  const normalized = value.trim().toUpperCase();
  return STOCK_CODE_PATTERNS.some((regex) => regex.test(normalized));
};

/**
 * Validate common A-share, HK, and US stock code formats.
 */
export const validateStockCode = (value: string): ValidationResult => {
  const normalized = value.trim().toUpperCase();

  if (!normalized) {
    return { valid: false, message: '请输入股票代码', normalized };
  }

  const valid = looksLikeStockCode(normalized);

  return {
    valid,
    message: valid ? undefined : '股票代码格式不正确',
    normalized,
  };
};

/**
 * Parse and validate comma-separated stock code input from the homepage.
 */
export const parseBatchStockCodes = (value: string): BatchStockCodeParseResult => {
  const normalized = value.trim().toUpperCase();

  if (!normalized || !BATCH_STOCK_CODE_SPLITTER.test(normalized)) {
    return { isBatch: false, codes: [], invalidCodes: [] };
  }

  const rawCodes = normalized
    .split(BATCH_STOCK_CODE_SPLITTER)
    .map((item) => item.trim())
    .filter(Boolean);

  if (rawCodes.length <= 1) {
    return { isBatch: false, codes: [], invalidCodes: [] };
  }

  const uniqueCodes: string[] = [];
  const seen = new Set<string>();
  const invalidCodes: string[] = [];

  for (const code of rawCodes) {
    const { valid, normalized: normalizedCode } = validateStockCode(code);
    if (!valid) {
      invalidCodes.push(code);
      continue;
    }

    if (!seen.has(normalizedCode)) {
      seen.add(normalizedCode);
      uniqueCodes.push(normalizedCode);
    }
  }

  return {
    isBatch: true,
    codes: uniqueCodes,
    invalidCodes,
  };
};

/**
 * Reject obviously invalid free-text queries before they reach the backend.
 */
export const isObviouslyInvalidStockQuery = (value: string): boolean => {
  const normalized = value.trim().toUpperCase();

  if (!normalized || looksLikeStockCode(normalized)) {
    return false;
  }

  if (!SUPPORTED_QUERY_CHARACTERS.test(normalized)) {
    return true;
  }

  const hasLetters = /[A-Z]/.test(normalized);
  const hasDigits = /\d/.test(normalized);

  return hasLetters && hasDigits;
};
