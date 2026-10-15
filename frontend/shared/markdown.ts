import MarkdownIt from 'markdown-it'
import type { DOMPurify } from 'dompurify'

const markdown = new MarkdownIt({html: false, breaks: true, linkify: true, typographer: false})
const validateLink = markdown.validateLink
markdown.validateLink = url => validateLink(url) && /^(?:https?:\/\/|mailto:|\/(?!\/)|#)/i.test(url)
const escape = markdown.utils.escapeHtml
markdown.renderer.rules.image = (tokens, index) =>
  `<span class="markdown-image-note">[图片：${escape(tokens[index].content || '未命名')}，未自动加载]</span>`
markdown.renderer.rules.link_open = (tokens, index, options, _env, renderer) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return renderer.renderToken(tokens, index, options)
}
const fence = markdown.renderer.rules.fence!
markdown.renderer.rules.fence = (tokens, index, options, env, renderer) => {
  const language = escape(tokens[index].info.trim().split(/\s+/)[0] || 'text')
  return `<div class="markdown-code"><div class="markdown-code-label">${language}</div>${fence(tokens, index, options, env, renderer)}</div>`
}

export function renderMarkdown(source: string, purifier: Pick<DOMPurify, 'sanitize'>): string {
  // Never render raw HTML, images, style, forms or executable URI schemes.
  // Both streaming and saved messages use this same final sanitizer boundary.
  return purifier.sanitize(markdown.render(source), {
    ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 's', 'del', 'a', 'ul', 'ol', 'li', 'blockquote',
      'pre', 'code', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'thead', 'tbody',
      'tr', 'th', 'td', 'div', 'span'],
    ALLOWED_ATTR: ['href', 'title', 'class', 'start', 'align', 'target', 'rel'],
    ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false,
    RETURN_TRUSTED_TYPE: false,
  })
}
