/** 不受理案沒有 F2 推薦;參考依據頁與草稿頁依據欄顯示同一句,款次解析不到就不寫死。 */
export function inadmissibleLawNote(screening) {
  const m = /第\s*(\d+)\s*款/.exec(screening?.matched_clause ?? '')
  return `本案經程序審查認定不受理，依訴願法第 77 條${m ? `第 ${m[1]} 款` : ''}逕為不受理決定，未進行法規推薦。`
}
