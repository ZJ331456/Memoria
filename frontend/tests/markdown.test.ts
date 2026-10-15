import { test } from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import createDOMPurify from 'dompurify'
import { renderMarkdown } from '../shared/markdown.ts'

const window = new JSDOM('').window
const purifier = createDOMPurify(window as any)
function render(text: string) {
  const host = window.document.createElement('div')
  host.innerHTML = renderMarkdown(text, purifier)
  return host
}

test('readable markdown preserves headings, lists, code, tables and citation text', () => {
  const text = '# 标题\n\n**重点**\n\n- 事项\n\n> 引用\n\n```python\nprint("<safe>")\n```\n\n| 项 | 值 |\n|---|---|\n| a | b |\n\n[memory:abc]'
  const host = render(text)
  for (const tag of ['h1','strong','li','blockquote','pre code','table']) assert.ok(host.querySelector(tag), tag)
  assert.equal(host.querySelector('pre code')?.textContent, 'print("<safe>")\n')
  assert.ok(host.textContent?.includes('[memory:abc]'))
  assert.equal(text.includes('**重点**'), true) // The source is never rewritten.
})

test('raw HTML, executable URLs and fence language injection stay inert', () => {
  const host = render('<img src=x onerror=alert(1)>\n<script>alert(2)</script>\n\n[x](javascript:alert(3))\n[y](data:text/html,test)\n[z](file:///private)\n\n```\"><img src=x onerror=alert(1)>\ncode\n```')
  assert.equal(host.querySelectorAll('script,img,iframe,svg,style,form,input,[onerror],[onclick]').length, 0)
  assert.equal(host.querySelectorAll('a[href]').length, 0)
})

test('external links are isolated and images never fetch a remote resource', () => {
  const host = render('[source](https://example.com/doc)\n\n![私人图片](https://example.com/tracker.png)')
  const link = host.querySelector('a')!
  assert.equal(link.getAttribute('target'), '_blank')
  assert.equal(link.getAttribute('rel'), 'noopener noreferrer')
  assert.equal(host.querySelectorAll('img,[src]').length, 0)
  assert.ok(host.textContent?.includes('未自动加载'))
})

test('every streaming prefix remains safe, including an unfinished code fence', () => {
  const text = '**流式**\n\n```html\n<img src=x onerror=alert(1)>\n```\n\n[safe](https://example.com)'
  for (let end = 1; end <= text.length; end++) {
    const host = render(text.slice(0,end))
    assert.equal(host.querySelectorAll('img,script,[onerror]').length, 0)
  }
})

test('only explicit web, mail and internal links keep a clickable destination', () => {
  for (const [href, allowed] of [
    ['https://example.com', true], ['/studio/#conversations', true], ['#source', true],
    ['mailto:test@example.com', true], ['//example.com', false], ['custom:secret', false],
    ['javascript&#58;alert(1)', false], ['jav&#x61;script:alert(1)', false],
  ] as const) {
    assert.equal(!!render(`[link](${href})`).querySelector('a[href]'), allowed, href)
  }
})
