import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

const publicHomeSource = readSource('../src/content/publicHome.ts')
const trustSource = readSource('../src/components/public/TrustAndDataBoundary.tsx')
const headerSource = readSource('../src/components/Header.tsx')
const siteSource = readSource('../src/config/site.ts')
const dialogSource = readSource('../src/components/OpenWechatContact.tsx')
const cardSource = readSource('../src/components/WechatQrCard.tsx')
const inlineSource = readSource('../src/components/ServiceWechatContact.tsx')

test('弹层说明只保留添加微信，公开页部署卡片不放入口', () => {
  assert.match(siteSource, /description: '私有化或内网部署请添加微信'/)
  assert.match(siteSource, /rechargeLabel: '私有化或内网部署'/)
  assert.doesNotMatch(siteSource, /请添加微信。|需要私有化|我们在微信上完成对接/)
  assert.doesNotMatch(siteSource, /pathLabel/)
  assert.doesNotMatch(publicHomeSource, /contactLabel/)
  assert.doesNotMatch(trustSource, /OpenWechatContact|企业部署，添加微信/)
})


test('微信入口展示二维码和微信号，不拉起微信客户端', () => {
  assert.match(dialogSource, /WechatQrCard/)
  assert.match(cardSource, /siteConfig\.serviceWechatQr/)
  assert.match(cardSource, /siteConfig\.serviceWechatId/)
  assert.match(inlineSource, /WechatQrCard/)
  assert.match(inlineSource, /triggerLabel = '紧急问题？联系客服'/)
  assert.doesNotMatch(`${dialogSource}\n${cardSource}\n${inlineSource}`, /weixin:\/\//)
})
