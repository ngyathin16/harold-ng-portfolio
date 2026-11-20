"""Bounded DFS steps. All pruning is optimistic; full results are recounted."""
try:
    from numba import njit
except ImportError:
    def njit(*args, **kwargs):
        return lambda fn: fn


@njit(cache=True)
def advance(memberships, groups, slots, costs, thresholds, required, forbidden,
            suffix, max_memberships, counts, picked, next_index, entered,
            slot_sums, cost_sums, depth, seed_size, minimum_size, maximum_size,
            capacity, minimum_active, minimum_cost, maximum_cost, budget, output):
    nodes = emitted = 0
    n, traits = memberships.shape
    while depth[0] >= 0:
        d = depth[0]
        start = next_index[d]
        if entered[d] == 0:
            nodes += 1
            entered[d] = 1
            size = seed_size + d
            remaining = maximum_size - size
            active = possible = 0
            required_ok = True
            viable = slot_sums[d] <= capacity and cost_sums[d] <= maximum_cost
            deficits = [0] * (remaining + 1)
            for t in range(traits):
                if counts[t] >= forbidden[t]:
                    viable = False
                if counts[t] < required[t]:
                    required_ok = False
                    if counts[t] + min(remaining, suffix[start, t]) < required[t]:
                        viable = False
                if counts[t] >= thresholds[t]:
                    active += 1
                    possible += 1
                else:
                    deficit = thresholds[t] - counts[t]
                    if deficit <= remaining and suffix[start, t] >= deficit:
                        possible += 1
                        deficits[deficit] += 1
            if possible < minimum_active or size + n - start < minimum_size:
                viable = False
            needed, contribution_budget = max(0, minimum_active - active), remaining * max_memberships[start]
            for deficit in range(1, remaining + 1):
                take = min(needed, deficits[deficit])
                contribution_budget -= take * deficit
                needed -= take
            if contribution_budget < 0:
                viable = False
            if viable and size >= minimum_size and active >= minimum_active and required_ok and cost_sums[d] >= minimum_cost:
                output[emitted, 0] = d
                for j in range(d):
                    output[emitted, j + 1] = picked[j]
                emitted += 1
            if not viable or remaining == 0:
                next_index[d] = n
            if emitted == len(output) or nodes >= budget:
                return emitted, nodes, False
        i = next_index[d]
        descended = False
        while i < n:
            next_index[d] = i + 1
            legal = slots[i] + slot_sums[d] <= capacity and costs[i] + cost_sums[d] <= maximum_cost
            for j in range(d):
                if groups[picked[j]] == groups[i]:
                    legal = False
            if legal:
                picked[d] = i
                for t in range(traits):
                    counts[t] += memberships[i, t]
                depth[0] = d + 1
                next_index[d + 1] = i + 1
                entered[d + 1] = 0
                slot_sums[d + 1] = slot_sums[d] + slots[i]
                cost_sums[d + 1] = cost_sums[d] + costs[i]
                descended = True
                break
            i += 1
        if not descended:
            if d:
                for t in range(traits):
                    counts[t] -= memberships[picked[d - 1], t]
            depth[0] = d - 1
    return emitted, nodes, True
