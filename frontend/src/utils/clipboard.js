/**
 * Copy text to the clipboard with a legacy fallback.
 *
 * The modern Clipboard API (navigator.clipboard) is only available in secure
 * contexts (HTTPS or localhost/127.0.0.1) and can also reject when the
 * document is not focused or clipboard-write permission is denied. In those
 * cases we fall back to the deprecated but widely supported
 * document.execCommand('copy') approach.
 *
 * @param {string} text - The text to copy
 * @returns {Promise<void>} Resolves when the text was copied, rejects if all methods fail
 */
export function copyTextToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text).catch(err => {
      // Clipboard API rejected (e.g. focus/permission issue) - try the fallback
      return legacyCopy(text).catch(() => Promise.reject(err))
    })
  }

  return legacyCopy(text)
}

/**
 * Legacy copy using a hidden textarea and document.execCommand('copy').
 * Works in non-secure contexts where the Clipboard API is unavailable.
 *
 * @param {string} text - The text to copy
 * @returns {Promise<void>}
 */
function legacyCopy(text) {
  return new Promise((resolve, reject) => {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.setAttribute('readonly', '')
    // Keep the element off-screen but still selectable
    textarea.style.position = 'fixed'
    textarea.style.top = '-9999px'
    textarea.style.left = '-9999px'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)

    textarea.select()
    textarea.setSelectionRange(0, textarea.value.length) // Required for iOS

    try {
      const successful = document.execCommand('copy')
      document.body.removeChild(textarea)
      if (successful) {
        resolve()
      } else {
        reject(new Error('document.execCommand("copy") returned false'))
      }
    } catch (err) {
      document.body.removeChild(textarea)
      reject(err)
    }
  })
}
