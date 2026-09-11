/** 爬蟲語料帶的原始查詢系統連結。沒有網址就不畫——點下去失敗的連結比沒有連結更糟
 *  (同 providers/aws.py 的 external_source_url 判準)。階段頁與草稿頁共用同一份。 */
export default function SourceSiteLink({ url }) {
  if (!url) return null
  return (
    <a className="btn-link" href={url} target="_blank" rel="noopener noreferrer">
      來源網站
    </a>
  )
}
