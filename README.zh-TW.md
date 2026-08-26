# devin-harness — 量測一群 agent 到底做了什麼

*[English version](README.md)（英文為主，內容較完整）*

客戶每次問的都是同一句：「我有 200 個檔案要遷移，agent 能不能直接做完？」誠實的答案不是能或
不能，而是**一個數字，附上明確定義，以及這個數字不包含什麼**。

這就是產生那個數字的工具。它讀一份 task manifest，透過 Devin v3 API 展開成 N 個 session，要求
每個 session 都遵守同一份可機器驗證的回報契約，最後輸出一張可以直接拿給工程主管看的表：

```
task manifest → N 個 session → 契約驗證過的回報 → 量測表 + 每個任務的判定
```

它刻意做得很小。有價值的不是並行，而是**它拒絕計算哪些指標**。

## 讓這張表可信的三個決定

1. **自述與外部證據永不合併。** session 的 structured output 只是「主張」；PR 是否存在、是否
   merged 來自 API 的 `pull_requests`。報表把兩欄並排，不會平均成一個「成功率」——兩者的落差
   才是結論。
2. **「自稱完成但沒有證據」自己一欄。** `unverified_completion_rate` 統計回報
   `outcome: completed`、但自己的驗證指令失敗，或該開 PR 卻沒開的 session。決定一個遷移可不可行
   的不是成功率，而是 agent 多常「以為」自己完成了。
3. **分母永遠是 task manifest。** 沒開起來、逾時、輸出違反 schema 的任務都留在分母裡。如果一個
   harness 在 session 早點壞掉時完成率會變好，那它量的是自己而不是 agent。

## 其他性質

- 每次狀態變更都先寫進 `journal.jsonl` 才發下一個 API 請求：指標從持久紀錄計算，crash 後可稽核。
- v3 create 沒有 idempotency key，所以用 `fanout-run:<run_id>` 與 `fanout-task:<task_id>` tag
  做去重；resume 時先用 tag 找回自己的 session，不會替同一個任務再開一個。
- 只有 `429` / `5xx` 會退避重試，`4xx` 立刻失敗——key 過期應該停下整個 run。
- live 模式不會退回 mock：API 連不上就讓 run 失敗，避免 mock 數字被當成 agent 實績引用。
- 這個 repo 裡 commit 的數字全部來自 mock transport，報表開頭就寫明。真實數字屬於在自己組織裡跑
  的人。

## 不做的事

不判斷改動是否正確（code review 仍然是關卡）、不自動 merge、不自動重跑失敗任務——「再跑一次」是
一個決定，不是預設值。

使用方式、契約欄位定義與檔案結構請看[英文版](README.md)。
