import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { SITE_URL } from '../src/config/site.ts'

const indexHtml = readFileSync(new URL('../index.html', import.meta.url), 'utf8')
const sitemapXml = readFileSync(new URL('../public/sitemap.xml', import.meta.url), 'utf8')
const robotsTxt = readFileSync(new URL('../public/robots.txt', import.meta.url), 'utf8')

test('site url is canonicalized without trailing double slash', () => {
  assert.equal(SITE_URL, 'https://www.bidmind-ai.com')
  assert.match(indexHtml, new RegExp(`${SITE_URL}/`))
  assert.match(sitemapXml, new RegExp(`${SITE_URL}/`))
  assert.match(robotsTxt, new RegExp(`${SITE_URL}/sitemap\\.xml`))
  const doubleSlashDomain = new RegExp('bidmind-ai\\\\.com//')
  assert.doesNotMatch(indexHtml, doubleSlashDomain)
  assert.doesNotMatch(sitemapXml, doubleSlashDomain)
  assert.doesNotMatch(robotsTxt, doubleSlashDomain)
})

test('seo metadata covers all three core capabilities without unsupported promises', () => {
  assert.match(indexHtml, /标书编写/)
  assert.match(indexHtml, /投标审查/)
  assert.match(indexHtml, /串标/)
  assert.doesNotMatch(indexHtml, /数据不出域/)
  assert.doesNotMatch(indexHtml, /\"price\"\\s*:\\s*\"0\"/)
})
