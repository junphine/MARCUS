/**
 * Helpers for normalizing chemical-structure values (SMILES / molfile).
 *
 * The OCSR endpoints (`/generate_smiles`, `/generate_molfile`, `/generate_both`)
 * now return `smiles` and `molfile` as arrays of strings (one entry per
 * detected molecule). These helpers make it easy for the rest of the app to
 * treat those values uniformly, whether a consumer needs a single string or a
 * list for display.
 */

/**
 * Normalize a smiles/molfile value (string | array | null/undefined) into an
 * array of non-empty strings.
 *
 * @param {string|string[]|null|undefined} value
 * @returns {string[]}
 */
export function toList(value) {
  if (value == null) return []
  if (Array.isArray(value)) {
    return value.filter((v) => typeof v === 'string' && v.trim() !== '')
  }
  if (typeof value === 'string') {
    return value.trim() !== '' ? [value] : []
  }
  return []
}

/**
 * Return the first (primary) string from a smiles/molfile value, or `fallback`.
 *
 * @param {string|string[]|null|undefined} value
 * @param {string} [fallback]
 * @returns {string}
 */
export function primary(value, fallback = '') {
  const list = toList(value)
  return list.length > 0 ? list[0] : fallback
}

/**
 * Join a smiles/molfile value into a single display string.
 *
 * @param {string|string[]|null|undefined} value
 * @param {string} [separator]
 * @returns {string}
 */
export function joinValue(value, separator = '\n') {
  return toList(value).join(separator)
}

/**
 * True if the value is a list containing more than one structure.
 *
 * @param {string|string[]|null|undefined} value
 * @returns {boolean}
 */
export function hasMultiple(value) {
  return toList(value).length > 1
}
