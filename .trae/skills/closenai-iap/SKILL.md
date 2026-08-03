---
name: "closenai-iap"
description: "ClosenAI IAP subscription: StoreKit 2, App Store Server API, receipt validation, react-native-iap, subscription tiers, App Store Connect setup. Invoke when working on in-app purchases, subscriptions, receipt verification, or App Store submission."
---

# ClosenAI IAP Subscription & Payment

IAP 订阅与支付规范：StoreKit 2 + App Store Server API + 收据校验 + react-native-iap + 订阅层级 + App Store Connect 配置

## Why IAP (Not Apple Pay)

| Technology | Applicable Goods | ClosenAI? |
|-----------|-----------------|-----------|
| **IAP (In-App Purchase)** | Virtual digital content: premium features, subscriptions, consumables | ✅ **Yes** — AI chat, personality profiles, Pro/Pro+ subscriptions |
| **Apple Pay** | Physical goods, external services, donations | ❌ No — ClosenAI sells virtual digital content |

**Apple rule**: Any app selling virtual digital content MUST use IAP and pay Apple's commission. ClosenAI's astrology AI chat, personality profiles, Pro/Pro+ subscriptions are in-app virtual digital content, so IAP is mandatory.

## Subscription Tiers

| Tier | Positioning | IAP Type | Suggested Price | Target |
|------|------------|----------|----------------|--------|
| **Free** | Acquisition & retention | — | $0 | New users, low-frequency users |
| **Pro** | Main revenue tier | Auto-renewable subscription | $7.99/mo or $59.99/yr | Daily active users |
| **Pro+** | Power user tier | Auto-renewable subscription | $14.99/mo or $99.99/yr | Heavy users, long-term |

### Tier Quotas

| Capability | Free | Pro | Pro+ |
|-----------|------|-----|------|
| Daily AI chats | 2 | 5 | 100 |
| Personality refresh | 3 (lifetime) | 5/month | Unlimited |
| Mood check-in | Basic (Rule Engine) | Deep | Deep |
| Long-term memory | Basic | Full | Full |
| Growth insights | Basic | Full | Full |
| Active messages (deep) | None | 1/day | Multiple/day |

**Design principles:**
- Free tier quotas intentionally low to motivate upgrade, but ensure basic experience is usable
- Present quotas as "剩余次数/明天再来" (natural language), avoid "配额" terminology
- Free AI chat uses Rule Engine + cache, LLM cost → $0, marginal cost negligible

## Implementation Phases

### Phase 0: App Store Connect Setup (Before Submission)
- [ ] Register & activate Apple Developer Program (Individual, $99/yr)
- [ ] Create App in App Store Connect
- [ ] Configure IAP products: Free / Pro / Pro+ as auto-renewable subscriptions (one subscription group)
- [ ] Configure pricing tiers, free trials, localization, upgrade/downgrade rules
- [ ] Complete Agreements, Tax, and Banking (Paid Applications Agreement, W-8BEN, bank account)

### Phase 1: Backend Data Model
- [ ] Create `subscriptions` / `receipts` tables
- [ ] Tiered quotas driven by subscription status, not hardcoded frontend

### Phase 2: Backend Receipt Validation (Security Core)
- [ ] Server-side validation via StoreKit 2 App Store Server API or `verifyReceipt`
- [ ] Grant entitlements only after successful validation
- [ ] Integrate App Store Server Notifications (V2) for real-time renew/refund/expire sync

### Phase 3: Client (RN/Expo)
- [ ] Use `react-native-iap` for purchase, restore, subscription status listening
- [ ] Send purchase/receipt results to backend for entitlement

### Phase 4: Compliance & Testing
- [ ] Sandbox test accounts: full flow (purchase, renew, refund, downgrade)
- [ ] Add subscription terms and privacy policy links in App Store Connect

## Backend Data Model

```sql
-- Subscription state
CREATE TABLE subscriptions (
  user_id UUID PRIMARY KEY REFERENCES users(id),
  tier TEXT NOT NULL,                 -- free / pro / pro+
  product_id TEXT,
  expires_at TIMESTAMPTZ,
  original_transaction_id TEXT,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Receipt audit trail
CREATE TABLE receipts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  transaction_id TEXT NOT NULL,
  product_id TEXT,
  payload JSONB,
  status TEXT NOT NULL,               -- pending / verified / refunded / expired
  created_at TIMESTAMPTZ DEFAULT now()
);
```

## Server-Side Receipt Validation

```python
# Backend endpoint: verify receipt from client
@router.post("/iap/verify")
async def verify_receipt(user_id: str, receipt_data: str, product_id: str):
    # Call App Store Server API (StoreKit 2)
    # or verifyReceipt endpoint for validation
    
    response = await verify_with_app_store(receipt_data)
    
    if response.status == "valid":
        # Update subscription
        await db.execute(
            "INSERT INTO subscriptions (user_id, tier, product_id, expires_at) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (user_id) DO UPDATE SET tier=$2, expires_at=$4",
            user_id, map_product_to_tier(product_id), product_id, response.expires_at
        )
        
        # Log receipt
        await db.execute(
            "INSERT INTO receipts (user_id, transaction_id, product_id, status) "
            "VALUES ($1, $2, $3, 'verified')",
            user_id, response.transaction_id, product_id
        )
        
        return {"status": "verified", "tier": map_product_to_tier(product_id)}
    else:
        return {"status": "invalid", "reason": response.error}
```

## App Store Server Notifications (V2)

```python
@router.post("/iap/notifications")
async def handle_app_store_notification(payload: dict):
    notification_type = payload.get("notificationType")
    
    if notification_type == "DID_RENEW":
        # Extend subscription
        await extend_subscription(payload)
    elif notification_type == "REFUND":
        # Revoke entitlements immediately
        await revoke_subscription(payload)
    elif notification_type == "EXPIRED":
        # Downgrade to free tier
        await downgrade_to_free(payload)
    elif notification_type == "DID_CHANGE_RENEWAL_STATUS":
        # Handle auto-renewal toggle
        await update_renewal_status(payload)
    
    return {"status": "ok"}
```

## Client-Side Purchase Flow

```typescript
import { 
  requestSubscription, 
  getSubscriptions,
  purchaseUpdatedListener,
  purchaseErrorListener 
} from 'react-native-iap';

// Get available products
const products = await getSubscriptions({
  skus: ['closenai_pro_monthly', 'closenai_pro_yearly', 
         'closenai_pro_plus_monthly', 'closenai_pro_plus_yearly']
});

// Initiate purchase
const purchase = await requestSubscription({
  sku: 'closenai_pro_monthly',
  andDangerouslyFinishTransactionAutomaticallyIOS: false,
});

// Send receipt to backend
const result = await api.verifyReceipt({
  receipt: purchase.transactionReceipt,
  productId: purchase.productId,
});

if (result.status === 'verified') {
  // Update local state
  await finishTransaction({ purchase, isConsumable: false });
}

// Restore purchases
const purchases = await getAvailablePurchases();
for (const purchase of purchases) {
  await api.verifyReceipt({ receipt: purchase.transactionReceipt });
}
```

## Pricing Strategy

- **Prefer annual**: Annual ≈ 6x monthly (e.g., $59.99/yr ≈ $5.00/mo). Boosts LTV and cash flow
- **Free trial**: 3-day trial to lower conversion barrier
- **Subscription group**: Pro and Pro+ in same group, supports upgrade/downgrade with proration
- **No consumables**: Don't use consumable IAP for chat quotas — users resist repeated purchases. Subscription fits "companion service" mindset better

## Key Metrics & Risk Control

| Metric | Target | Notes |
|--------|--------|-------|
| Free → Paid conversion | ≥ 5% | Replika benchmark: 5-8% |
| Monthly churn | < 10% | Subscription health |
| Refund rate | < 2% | Above threshold needs Server Notifications validation |
| Annual plan share | ≥ 30% | Boosts LTV |

**Risk controls:**
- Backend must validate receipts — prevent client-side forgery/bypass
- Hook into refund notifications — revoke entitlements immediately on refund
- Multi-language pricing (Chinese/English/Indonesian) adjusted per local market

## Financial Requirements (App Store Connect)

Before submission, complete in App Store Connect → Agreements, Tax, and Banking:

| Item | Description | Individual OK? |
|------|-------------|----------------|
| Paid Applications Agreement | Entry requirement for paid apps | ✅ |
| Tax info (W-8BEN) | China developer declares non-US taxpayer, avoids 30% withholding | ✅ |
| Bank account | Apple payout destination | ✅ |
| Contact info | Real English address | ✅ |

**Small Business Program**: If annual revenue < $1M, apply for 15% commission (down from 30%). Individual developers qualify.