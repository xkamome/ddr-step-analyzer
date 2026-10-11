'use strict';
// 介面文字（中文／日文）。分析建議本文由後端產生，維持中文。
const I18N = {
  zh: {
    'app.title': 'DDR 踩點分析', 'add.btn': '＋ 新增影片', 'sel.aria': '選擇影片',
    'offline': '伺服器已停止。請重新執行 start.bat（或桌面捷徑）。',
    'empty': '選擇或新增一支影片', 'loading': '載入分析中…', 'load.fail': '載入失敗',
    'calib.hint': '拖<b>箭頭圓點</b>（←↓↑→ 板中心）或<b>黃色四角</b>對齊踏板，拖格子內部可整塊移動；滾輪放大、拖空白處平移、雙擊復原。如果九宮格是對的、但腳的位置整體偏掉，可以<b>拖右上的俯瞰圖</b>只移動腳（九宮格不動）。踏板只拍到一部分時，按「點選對應點」最準。',
    'arm.est': '推定', 'pad.footOff': '腳位置偏移 右{x} 後{y} cm',
    'calib.clickBtn': '點選對應點',
    'calib.pickPrompt': '① 在<b>右邊俯瞰圖</b>點你看得到的<b>角或中心</b>（黃框是目前選的；沒選就依序用 ← ↓ ↑ → 的中心）→ ② 到<b>影片上</b>點它的實際位置。已點 <b>{n}</b> 點，至少 4 點（不要全在同一條線上）後按「完成」。可以先換到亮燈的畫面再點；按 Esc 取消。',
    'calib.pickDone': '完成（{n} 點）', 'calib.pickBad': '這些點太接近一直線，請多點幾個不同位置的點。',
    'calib.rotate': '旋轉 90°', 'calib.apply': '套用', 'calib.applying': '重新分析中…', 'calib.reset': '重設為自動',
    'calib.cancel': '取消', 'calib.btn': '校正踏板', 'calib.active': '校正中…', 'calib.fail': '校正失敗：',
    'ctl.play': '播放／暫停（空白鍵）', 'ctl.prev': '上一幀（←）', 'ctl.next': '下一幀（→）', 'ctl.rate': '播放速度', 'ctl.seek': '時間軸',
    'layer.skel': '骨架', 'layer.com': '重心', 'layer.grid': '踏板格線', 'layer.feet': '落地點',
    'charts.title': '時間軸', 'win.8': '前後 4 秒', 'win.30': '30 秒', 'win.all': '全部', 'charts.hint': '點圖可跳到該時間',
    'pad.title': '俯視踏板', 'pad.showAll': '顯示全部落點',
    'pad.rotate': '俯視圖轉 90°（預設依影片中螢幕的方向自動轉；轉回自動的角度就恢復自動）', 'pad.rotAuto': '（自動）', 'lg.L': '左腳', 'lg.R': '右腳', 'lg.now': '目前落地', 'lg.com': '重心',
    'lg.heelUp': '虛線腳跟＝腳跟離地',
    'tab.advice': '修正建議', 'tab.metrics': '指標', 'tab.steps': '落點', 'tab.events': '重心事件',
    'dlg.title': '新增影片', 'dlg.path': '本機路徑', 'dlg.or': '或', 'dlg.file': '上傳檔案', 'dlg.q': '辨識品質',
    'dlg.q.acc': '準確（約 0.3 秒／幀）', 'dlg.q.fast': '快速（約 0.03 秒／幀，遮擋時較易左右腳混淆）',
    'dlg.hint': '拍攝建議：從玩家斜後上方、手機約腰部高度，拍到整個踏板與全身，四個方向的落點最準。',
    'dlg.cancel': '取消', 'dlg.go': '開始分析', 'dlg.need': '請輸入路徑或選擇檔案', 'dlg.uploading': '上傳中…', 'dlg.fail': '失敗',
    'st.queued': '排隊中', 'st.preview': '轉檔中', 'st.pose': '辨識中', 'st.analyze': '分析中', 'st.processing': '處理中',
    'st.error': '（錯誤）', 'st.eta': '，約剩 {m} 分',
    'b.homo': '視角：可見踏板（四方向）', 'b.side': '視角：貼地側拍（只可靠判斷前後）', 'b.none': '踏板未校正',
    'b.manual': '校正：手動', 'b.glow': '校正：自動（踏板亮燈）', 'b.feet': '校正：自動（腳印分群，僅供參考）', 'b.nocal': '校正：無',
    'b.trunk': '重心：軀幹＋下肢', 'b.pelvis': '重心：骨盆推估（上半身不在畫面）', 'b.steps': '{n} 步・{r} 步/秒', 'b.armEst': '{s}{p}% 推定（用另一手鏡像）', 'foot.Lh': '左手', 'foot.Rh': '右手',
    'pad.screen': '螢幕方向', 'pad.noCalib': '落點太少，無法校正踏板', 'pad.sideNote': '左右位置為推估',
    'lane.com': '重心', 'lane.hip': '骨盆高度', 'lane.steps': '落地', 'lane.lights': '踏板亮燈', 'short.L': '左', 'short.R': '右',
    'dir': '偏{x}', 'row': '{a} 列',
    'now.frame': '第 {i} 幀', 'foot.L': '左腳', 'foot.R': '右腳', 'now.planted': '落地', 'now.moving': '移動中',
    'now.lit': '・亮燈確認', 'now.heelUp': '腳跟離地 {h} cm', 'now.heelDown': '腳跟著地', 'now.conf': '信心 {c}',
    'now.com': '重心（{axis}）', 'now.rel': '，相對雙腳{d} {v} 格', 'now.lights': '亮燈', 'now.knee': '膝角', 'unit.panel': '格',
    'conf.high': '高', 'conf.medium': '中', 'conf.low': '低',
    'lv.bad': '需改善', 'lv.warn': '注意', 'lv.info': '參考', 'lv.good': '良好', 'adv.fix': '怎麼改',
    'adv.none': '資料不足，無法產生建議。',
    'adv.note': '建議由數據門檻自動產生（高階玩家標準）。所有 cm 數值以一格約 28 cm、小腿約 38 cm 換算，是估計值。',
    'm.duration': '影片長度', 'm.steps': '落地次數', 'm.rate': '步頻', 'm.comAxis': '重心可量方向',
    'm.comSway': '重心晃動（標準差）', 'm.comBias': '重心平均偏移（相對踏板中心）', 'm.comRel': '重心相對雙腳中點',
    'm.comCenter': '重心在中央板範圍', 'm.bounce': '骨盆彈跳', 'm.liftMed': '抬腳高度中位數', 'm.liftP90': '抬腳高度 P90',
    'm.liftL': '左腳抬高', 'm.liftR': '右腳抬高', 'm.knee': '落地膝角中位數', 'm.edge': '只壓到板緣', 'm.off': '踩到角落／板外',
    'm.heelUp': '腳跟離地比例', 'm.events': '重心大幅移動',
    'm.hold': '手腕固定比例（推測扶桿）：左 {l}%、右 {r}%',
    'u.sec': ' 秒', 'u.sps': ' 步/秒', 'u.panel': ' 格', 'u.pctLeg': ' % 腿長', 'u.steps': ' 步', 'u.times': ' 次', 'u.events': ' 處',
    'u.posAxis': '（+ 為{x}）',
    's.arrow': '箭頭', 's.spreadHead': '步數・散佈・平均偏移（格）',
    's.spreadNote': '平均偏移：x 為 + 表示偏右、y 為 + 表示偏後。自動（腳印分群）校正時，偏移是相對於自己的平均落點。',
    's.litTitle': '踏板亮燈（機台判定）', 's.litSteps': '亮燈確認的步數', 's.lightOnly': '其中只靠亮燈抓到（骨架沒偵測到落地）',
    's.agree': '骨架判斷與亮燈一致', 's.untouched': '亮燈了但骨架的腳沒碰到該板', 's.footExt': '亮燈判定寬容範圍（腳跟／腳尖延伸）', 's.shoe': '俯視圖鞋長（骨架腳長 × 1.1，估）',
    's.fit': '校正前 → 後一致率', 's.partOf': '{a} 用哪裡踩', 's.mm': '亮 {a}／骨架 {b}',
    's.mmNote': '「亮 ↓／骨架 中央」這類不一致，代表骨架上的腳沒碰到亮起的板；腳長校正就是為了修正這種情況（例如用腳跟踩 ↓）。',
    's.heelTitle': '腳跟離地（只算中／高信心的步）', 's.heelRow': '{p}% 腳跟離地（{n} 步）', 's.conf': '信心等級',
    's.confRow': '高 {h}・中 {m}・低 {l}', 's.confNote': '信心依據：亮燈確認、腳部點信心、腳在畫面上沒被壓縮、兩種腳跟指標一致、腳壓進板內夠深。',
    's.counts': '各板步數', 's.none': '沒有落點資料。',
    'ev.title': '重心大幅移動', 'ev.desc': '以你慣用的姿勢（{axis}方向平均 {b} cm）為基準，偏離最明顯的 {n} 段。點一下跳到該處並以 0.25× 慢放，俯視圖會畫出該段的重心軌跡。',
    'ev.type.travel': '整個人移過去', 'ev.type.balance': '重心跑出雙腳', 'ev.noCause': '無明顯原因',
    'ev.c.crossover': '交叉步', 'ev.c.jump': '跳', 'ev.c.same_foot': '同一腳連踩', 'ev.c.wide': '大跨距',
    'ev.c.bar_release': '手離開扶桿', 'ev.c.fast': '比平常密集',
    'ev.statsTitle': '哪種配置最容易讓重心跑掉', 'ev.statsDesc': '每種三步配置之後 0.35 秒內重心偏離的平均（出現 5 次以上）；倍數是和全曲平均比。',
    'ev.pat': '平均 {cm} cm・{r} 倍・{n} 次', 'ev.causeTitle': '原因統計', 'ev.clear': '清除選取', 'ev.none': '沒有找到明顯的重心移動。',
    'ev.selected': '事件 #{k}', 'ev.types': '整個人移過去 {a}・重心跑出雙腳 {b}',
  },
  ja: {
    'app.title': 'DDR ステップ解析', 'add.btn': '＋ 動画を追加', 'sel.aria': '動画を選択',
    'offline': 'サーバーが停止しました。start.bat（またはデスクトップのショートカット）をもう一度実行してください。',
    'empty': '動画を選択または追加してください', 'loading': '解析結果を読み込み中…', 'load.fail': '読み込みに失敗しました',
    'calib.hint': '<b>矢印の丸</b>（←↓↑→ パネルの中心）か<b>黄色の四隅</b>をドラッグしてパネルに合わせます。内側のドラッグで全体を移動、ホイールで拡大、空白部分のドラッグで表示を移動、ダブルクリックで元に戻ります。九マスは合っているのに足の位置が全体的にずれている場合は、<b>右上の俯瞰図をドラッグ</b>すると足だけを動かせます（九マスは動きません）。パネルが一部しか映っていないときは「対応点をクリック」が最も正確です。',
    'arm.est': '推定', 'pad.footOff': '足の位置オフセット 右{x} 後{y} cm',
    'calib.clickBtn': '対応点をクリック',
    'calib.pickPrompt': '① <b>右の俯瞰図</b>で、映っている<b>角か中心</b>をクリック（黄色の枠が選択中。選ばない場合は ← ↓ ↑ → の中心の順）→ ② <b>動画上</b>でその実際の位置をクリック。現在 <b>{n}</b> 点。4 点以上（一直線に並ばないように）になったら「完了」。点灯している場面に移動してからクリックしても OK。Esc で中止。',
    'calib.pickDone': '完了（{n} 点）', 'calib.pickBad': '点がほぼ一直線に並んでいます。別の位置の点を追加してください。',
    'calib.rotate': '90°回転', 'calib.apply': '適用', 'calib.applying': '再解析中…', 'calib.reset': '自動に戻す',
    'calib.cancel': 'キャンセル', 'calib.btn': 'パネル補正', 'calib.active': '補正中…', 'calib.fail': '補正に失敗しました：',
    'ctl.play': '再生／一時停止（スペース）', 'ctl.prev': '前のフレーム（←）', 'ctl.next': '次のフレーム（→）', 'ctl.rate': '再生速度', 'ctl.seek': 'シーク',
    'layer.skel': '骨格', 'layer.com': '重心', 'layer.grid': 'パネル格子', 'layer.feet': '着地点',
    'charts.title': 'タイムライン', 'win.8': '前後4秒', 'win.30': '30秒', 'win.all': '全体', 'charts.hint': 'クリックでその時点へ移動',
    'pad.title': 'パネル俯瞰図', 'pad.showAll': 'すべての着地点を表示',
    'pad.rotate': '俯瞰図を 90° 回転（初期値は動画内の画面の向きに合わせて自動。自動の角度に戻すと自動に戻ります）', 'pad.rotAuto': '（自動）', 'lg.L': '左足', 'lg.R': '右足', 'lg.now': '現在の着地', 'lg.com': '重心',
    'lg.heelUp': '点線のかかと＝かかとが浮いている',
    'tab.advice': 'アドバイス', 'tab.metrics': '指標', 'tab.steps': '着地', 'tab.events': '重心イベント',
    'dlg.title': '動画を追加', 'dlg.path': 'ローカルパス', 'dlg.or': 'または', 'dlg.file': 'ファイルをアップロード', 'dlg.q': '認識精度',
    'dlg.q.acc': '高精度（約0.3秒／フレーム）', 'dlg.q.fast': '高速（約0.03秒／フレーム。足が隠れると左右を取り違えやすい）',
    'dlg.hint': '撮影のコツ：プレイヤーの斜め後ろ上方から、スマホを腰の高さに構えて、パネル全体と全身を映すと四方向の着地点が最も正確になります。',
    'dlg.cancel': 'キャンセル', 'dlg.go': '解析開始', 'dlg.need': 'パスを入力するかファイルを選択してください', 'dlg.uploading': 'アップロード中…', 'dlg.fail': '失敗しました',
    'st.queued': '待機中', 'st.preview': '変換中', 'st.pose': '骨格認識中', 'st.analyze': '解析中', 'st.processing': '処理中',
    'st.error': '（エラー）', 'st.eta': '・残り約{m}分',
    'b.homo': '視点：パネルが見える（四方向）', 'b.side': '視点：床すれすれの真横（前後のみ信頼可）', 'b.none': 'パネル未補正',
    'b.manual': '補正：手動', 'b.glow': '補正：自動（パネル点灯）', 'b.feet': '補正：自動（足跡クラスタ・参考値）', 'b.nocal': '補正：なし',
    'b.trunk': '重心：体幹＋下肢', 'b.pelvis': '重心：骨盤から推定（上半身が画面外）', 'b.steps': '{n}歩・{r}歩/秒', 'b.armEst': '{s}は {p}% 推定（反対の手を鏡像）', 'foot.Lh': '左手', 'foot.Rh': '右手',
    'pad.screen': '画面方向', 'pad.noCalib': '着地点が少なくパネル補正できません', 'pad.sideNote': '左右位置は推定',
    'lane.com': '重心', 'lane.hip': '骨盤の高さ', 'lane.steps': '着地', 'lane.lights': 'パネル点灯', 'short.L': '左', 'short.R': '右',
    'dir': '{x}寄り', 'row': '{a} 列',
    'now.frame': '{i} フレーム目', 'foot.L': '左足', 'foot.R': '右足', 'now.planted': '着地', 'now.moving': '移動中',
    'now.lit': '・点灯で確認', 'now.heelUp': 'かかと浮き {h} cm', 'now.heelDown': 'かかと接地', 'now.conf': '信頼度 {c}',
    'now.com': '重心（{axis}）', 'now.rel': '、両足に対して{d} {v} マス', 'now.lights': '点灯', 'now.knee': '膝角度', 'unit.panel': 'マス',
    'conf.high': '高', 'conf.medium': '中', 'conf.low': '低',
    'lv.bad': '要改善', 'lv.warn': '注意', 'lv.info': '参考', 'lv.good': '良好', 'adv.fix': '改善方法',
    'adv.none': 'データ不足のためアドバイスを生成できません。',
    'adv.note': 'アドバイスはデータのしきい値から自動生成しています（上級者基準）。cm の値は 1 マス約 28 cm、すね約 38 cm として換算した推定値です。',
    'm.duration': '動画の長さ', 'm.steps': '着地回数', 'm.rate': 'ステップ頻度', 'm.comAxis': '重心を測れる方向',
    'm.comSway': '重心の揺れ（標準偏差）', 'm.comBias': '重心の平均のずれ（パネル中心基準）', 'm.comRel': '重心（両足の中点基準）',
    'm.comCenter': '重心が中央パネル内にある割合', 'm.bounce': '骨盤の上下動', 'm.liftMed': '足上げの高さ（中央値）', 'm.liftP90': '足上げの高さ（P90）',
    'm.liftL': '左足の上げ高さ', 'm.liftR': '右足の上げ高さ', 'm.knee': '着地時の膝角度（中央値）', 'm.edge': 'パネルの端だけを踏んだ割合', 'm.off': '角・パネル外',
    'm.heelUp': 'かかとが浮いていた割合', 'm.events': '重心の大きな移動',
    'm.hold': '手首が固定されていた割合（バー使用と推定）：左 {l}%・右 {r}%',
    'u.sec': ' 秒', 'u.sps': ' 歩/秒', 'u.panel': ' マス', 'u.pctLeg': ' %（脚長比）', 'u.steps': ' 歩', 'u.times': ' 回', 'u.events': ' 件',
    'u.posAxis': '（＋は{x}）',
    's.arrow': '矢印', 's.spreadHead': '歩数・ばらつき・平均のずれ（マス）',
    's.spreadNote': '平均のずれ：x が＋なら右寄り、y が＋なら後ろ寄り。自動（足跡クラスタ）補正の場合、ずれは自分の平均着地点との比較です。',
    's.litTitle': 'パネル点灯（筐体の判定）', 's.litSteps': '点灯で確認した歩数', 's.lightOnly': 'うち点灯だけで検出（骨格では着地を検出できず）',
    's.agree': '骨格判定と点灯の一致率', 's.untouched': '点灯したのに骨格の足がそのパネルに触れていない', 's.footExt': '点灯判定の許容範囲（かかと／つま先の延長）', 's.shoe': '俯瞰図の靴の長さ（骨格の足長 × 1.1・推定）',
    's.fit': '補正前 → 後の一致率', 's.partOf': '{a} を踏んだ部位', 's.mm': '点灯 {a}／骨格 {b}',
    's.mmNote': '「点灯 ↓／骨格 中央」のような不一致は、骨格上の足が点灯したパネルに触れていないことを示します。足の長さ補正はこれ（例：かかとで ↓ を踏む）を直すためのものです。',
    's.heelTitle': 'かかとの浮き（信頼度 中・高の歩のみ）', 's.heelRow': 'かかと浮き {p}%（{n}歩）', 's.conf': '信頼度',
    's.confRow': '高 {h}・中 {m}・低 {l}', 's.confNote': '信頼度の根拠：点灯での確認、足の点の信頼度、足が画面上で潰れていないか、2種類のかかと指標の一致、パネルに十分踏み込んでいるか。',
    's.counts': 'パネル別の歩数', 's.none': '着地データがありません。',
    'ev.title': '重心の大きな移動', 'ev.desc': 'いつもの姿勢（{axis}方向の平均 {b} cm）を基準に、ずれが最も大きかった {n} 区間です。クリックでその場面へ移動して 0.25 倍速で再生し、俯瞰図にその区間の重心の軌跡を描きます。',
    'ev.type.travel': '体ごと移動', 'ev.type.balance': '重心が両足から外れた', 'ev.noCause': '目立った原因なし',
    'ev.c.crossover': 'クロス（交差）', 'ev.c.jump': 'ジャンプ', 'ev.c.same_foot': '同じ足で連続', 'ev.c.wide': '大きな踏み替え',
    'ev.c.bar_release': 'バーから手を離した', 'ev.c.fast': '普段より高密度',
    'ev.statsTitle': '重心が崩れやすい配置', 'ev.statsDesc': '3歩の並びごとに、直後 0.35 秒間の重心のずれを平均（5回以上出現したもの）。倍率は曲全体の平均との比較です。',
    'ev.pat': '平均 {cm} cm・{r} 倍・{n} 回', 'ev.causeTitle': '原因の内訳', 'ev.clear': '選択解除', 'ev.none': '目立った重心の移動は見つかりませんでした。',
    'ev.selected': 'イベント #{k}', 'ev.types': '体ごと移動 {a}・重心が両足から外れた {b}',
  },
};
// 後端回傳的短標籤（中文）→ 翻譯
const VAL = {
  ja: {
    '中央': '中央', '角落': '角', '板外': 'パネル外', '中間列': '中段',
    '腳尖': 'つま先', '腳跟': 'かかと', '全腳': '足裏全体', '腳掌中段': '土踏まず付近', '未碰到': '未接触',
    '重心': '重心', '省力': '省エネ', '踩點': 'ステップ', '其他': 'その他',
    '左右': '左右', '前後': '前後', '斜向（↖↘）': '斜め（↖↘）', '斜向（↗↙）': '斜め（↗↙）',
    '右': '右', '左': '左', '後': '後ろ', '前': '前',
    '右後（→↓ 之間）': '右後ろ（→↓ の間）', '左前（←↑ 之間）': '左前（←↑ の間）',
    '右前（→↑ 之間）': '右前（→↑ の間）', '左後（←↓ 之間）': '左後ろ（←↓ の間）',
  },
};
let LANG = 'ja';                                         // 預設日文；使用者切換過就記住
try { LANG = localStorage.getItem('ddr-lang') || 'ja'; } catch (e) { /* 無痕模式等 */ }
if (!I18N[LANG]) LANG = 'ja';

function t(key, vars) {
  let s = I18N[LANG][key] ?? I18N.zh[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(v);
  return s;
}
const tv = v => (v == null ? v : (VAL[LANG] && VAL[LANG][v]) ?? v);

function applyI18n() {
  document.documentElement.lang = LANG === 'ja' ? 'ja' : 'zh-Hant-TW';
  document.title = t('app.title');
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-html]').forEach(el => { el.innerHTML = t(el.dataset.i18nHtml); });
  document.querySelectorAll('[data-i18n-title]').forEach(el => { el.title = t(el.dataset.i18nTitle); });
  document.querySelectorAll('[data-i18n-aria]').forEach(el => { el.setAttribute('aria-label', t(el.dataset.i18nAria)); });
}
function setLang(l) {
  LANG = I18N[l] ? l : 'ja';
  try { localStorage.setItem('ddr-lang', LANG); } catch (e) { /* 忽略 */ }
  applyI18n();
}
