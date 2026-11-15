# Evaluation report

Scenario: `checkout-service`

## Belief governance

false trust is the share of already-stale specs still returned as trusted. availability is the share of still-true specs the system is willing to hand out. A cautious system can drive the first to zero by refusing everything, so both are reported.

| condition      | family   | mark P | mark R | mark F1 | reviews/hit | false trust          | availability         | pending/step |
| -------------- | -------- | ------ | ------ | ------- | ----------- | -------------------- | -------------------- | ------------ |
| specgraph      | system   | 0.500  | 0.800  | 0.615   | 2.00        | 0.211 [0.067, 0.317] | 1.000 [1.000, 1.000] | 0.00         |
| static_memory  | baseline | -      | 0.000  | -       | -           | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.00         |
| ttl_memory     | baseline | -      | 0.000  | -       | -           | 0.947 [0.900, 1.000] | 0.828 [0.500, 0.944] | 0.00         |
| struct_only    | baseline | 0.500  | 0.600  | 0.545   | 2.00        | 0.000 [0.000, 0.000] | 0.724 [0.619, 0.833] | 0.00         |
| no_propagation | ablation | 0.500  | 0.400  | 0.444   | 2.00        | 0.526 [0.183, 0.583] | 1.000 [1.000, 1.000] | 0.00         |
| no_temporal    | ablation | 0.500  | 0.800  | 0.615   | 2.00        | 0.211 [0.067, 0.317] | 1.000 [1.000, 1.000] | 0.00         |
| no_verify      | ablation | 0.500  | 0.600  | 0.545   | 2.00        | 0.000 [0.000, 0.000] | 0.724 [0.619, 0.833] | 4.50         |
| file_anchors   | ablation | 0.444  | 0.800  | 0.571   | 2.25        | 0.105 [0.000, 0.283] | 1.000 [1.000, 1.000] | 0.00         |

## Task utility

harmful counts answers that were given and wrong, which is the failure mode a permissive memory produces. abstain is the share of questions the policy refused, which is what a governed memory trades harmful answers for.

| condition      | policy       | answer acc | abstain | harmful | files/task |
| -------------- | ------------ | ---------- | ------- | ------- | ---------- |
| specgraph      | no_memory    | 1.000      | 0.000   | 0.000   | 1.20       |
| specgraph      | trusted_only | 0.667      | 0.400   | 0.200   | 0.00       |
| specgraph      | blind        | 0.400      | 0.000   | 0.600   | 0.00       |
| static_memory  | no_memory    | 1.000      | 0.000   | 0.000   | 1.20       |
| static_memory  | trusted_only | 0.400      | 0.000   | 0.600   | 0.00       |
| static_memory  | blind        | 0.400      | 0.000   | 0.600   | 0.00       |
| ttl_memory     | no_memory    | 1.000      | 0.000   | 0.000   | 1.20       |
| ttl_memory     | trusted_only | 0.400      | 0.000   | 0.600   | 0.00       |
| ttl_memory     | blind        | 0.400      | 0.000   | 0.600   | 0.00       |
| struct_only    | no_memory    | 1.000      | 0.000   | 0.000   | 1.20       |
| struct_only    | trusted_only | -          | 1.000   | 0.000   | 0.00       |
| struct_only    | blind        | 0.400      | 0.000   | 0.600   | 0.00       |
| no_propagation | no_memory    | 1.000      | 0.000   | 0.000   | 1.20       |
| no_propagation | trusted_only | 0.667      | 0.400   | 0.200   | 0.00       |
| no_propagation | blind        | 0.400      | 0.000   | 0.600   | 0.00       |
| no_temporal    | no_memory    | 1.000      | 0.000   | 0.000   | 1.20       |
| no_temporal    | trusted_only | 0.667      | 0.400   | 0.200   | 0.00       |
| no_temporal    | blind        | 0.400      | 0.000   | 0.600   | 0.00       |
| no_verify      | no_memory    | 1.000      | 0.000   | 0.000   | 1.20       |
| no_verify      | trusted_only | -          | 1.000   | 0.000   | 0.00       |
| no_verify      | blind        | 0.400      | 0.000   | 0.600   | 0.00       |
| file_anchors   | no_memory    | 1.000      | 0.000   | 0.000   | 1.20       |
| file_anchors   | trusted_only | 0.667      | 0.400   | 0.200   | 0.00       |
| file_anchors   | blind        | 0.400      | 0.000   | 0.600   | 0.00       |

