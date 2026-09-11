<script setup lang="ts">
import { computed } from 'vue'

import type { AgentResult } from '../../agent-api'
import DeliveryTimeResult from './DeliveryTimeResult.vue'
import EvidenceResult from './EvidenceResult.vue'
import PostageResult from './PostageResult.vue'
import TrackingResult from './TrackingResult.vue'
import ProductPriceResult from './ProductPriceResult.vue'


const props = defineProps<{ result: AgentResult }>()
const data = computed(() => props.result.data ?? {})
</script>

<template>
  <TrackingResult
    v-if="result.type === 'tracking'"
    :data="data"
    :status="result.status"
    :provenance="result.provenance ?? []"
  />
  <DeliveryTimeResult v-else-if="result.type === 'delivery_time'" :data="data" />
  <PostageResult v-else-if="result.type === 'postage'" :data="data" :status="result.status" :basis="result.quote_basis ?? null" />
  <ProductPriceResult v-else-if="result.type === 'product_price'" :data="data" />
  <EvidenceResult
    v-else
    :type="result.type"
    :data="data"
  />
</template>
