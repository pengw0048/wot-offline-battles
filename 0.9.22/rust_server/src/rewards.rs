use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const PARTICIPATION_CREDITS_PER_TIER: u64 = 1_000;
pub const DAMAGE_CREDITS_PER_POINT: u64 = 2;
pub const ASSIST_CREDITS_PER_POINT: u64 = 1;
pub const SPOTTING_CREDITS: u64 = 100;
pub const CAPTURE_CREDITS_PER_POINT: u64 = 10;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct RewardStatistics {
    pub damage_dealt: u64,
    pub damage_assisted_track: u64,
    pub damage_assisted_radio: u64,
    pub damage_assisted_stun: u64,
    pub kills: u64,
    pub spotted: u64,
    pub capture_points: u64,
    pub dropped_capture_points: u64,
}

impl RewardStatistics {
    /// Compatibility adapter for the Python receipt/statistics dictionary.
    /// Invalid and negative values become zero, as in `offline_rewards.py`.
    pub fn from_wire(value: &Value) -> Self {
        let get = |name| {
            value
                .get(name)
                .and_then(python_nonnegative_int)
                .unwrap_or(0)
        };
        Self {
            damage_dealt: get("damage_dealt"),
            damage_assisted_track: get("damage_assisted_track"),
            damage_assisted_radio: get("damage_assisted_radio"),
            damage_assisted_stun: get("damage_assisted_stun"),
            kills: get("kills"),
            spotted: get("spotted"),
            capture_points: get("capture_points"),
            dropped_capture_points: get("dropped_capture_points"),
        }
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct OfflineRewards {
    pub credits: u64,
    pub xp: u64,
    pub free_xp: u64,
    pub repair_cost: u64,
    pub ammo_cost: u64,
}

/// Explicit offline economy policy. These coefficients are deliberately not
/// represented as the proprietary retail 0.9.22 formula.
pub fn compute_offline_rewards(
    statistics: RewardStatistics,
    won: bool,
    participated: bool,
    vehicle_tier: u8,
) -> OfflineRewards {
    let assist = statistics
        .damage_assisted_track
        .saturating_add(statistics.damage_assisted_radio)
        .saturating_add(statistics.damage_assisted_stun);
    let participation_xp: u64 = if participated { 100 } else { 0 };
    let base_xp = participation_xp
        .saturating_add(statistics.damage_dealt / 5)
        .saturating_add(assist / 10)
        .saturating_add(statistics.kills.saturating_mul(100))
        .saturating_add(statistics.spotted.saturating_mul(20))
        .saturating_add(statistics.capture_points.saturating_mul(2))
        .saturating_add(statistics.dropped_capture_points.saturating_mul(2));
    let combat_xp = if won {
        base_xp.saturating_mul(3) / 2
    } else {
        base_xp
    };

    let tier = u64::from(vehicle_tier.clamp(1, 10));
    let mut participation_credits = if participated {
        PARTICIPATION_CREDITS_PER_TIER.saturating_mul(tier)
    } else {
        0
    };
    if won {
        participation_credits = participation_credits.saturating_mul(185) / 100;
    }
    let credits = participation_credits
        .saturating_add(
            statistics
                .damage_dealt
                .saturating_mul(DAMAGE_CREDITS_PER_POINT),
        )
        .saturating_add(assist.saturating_mul(ASSIST_CREDITS_PER_POINT))
        .saturating_add(statistics.spotted.saturating_mul(SPOTTING_CREDITS))
        .saturating_add(
            statistics
                .capture_points
                .saturating_mul(CAPTURE_CREDITS_PER_POINT),
        );

    OfflineRewards {
        credits,
        xp: combat_xp,
        free_xp: combat_xp.saturating_mul(5) / 100,
        repair_cost: 0,
        ammo_cost: 0,
    }
}

fn python_nonnegative_int(value: &Value) -> Option<u64> {
    match value {
        Value::Null => Some(0),
        Value::Bool(value) => Some(u64::from(*value)),
        Value::Number(number) => {
            if let Some(value) = number.as_u64() {
                Some(value)
            } else if let Some(value) = number.as_i64() {
                Some(value.max(0) as u64)
            } else {
                let value = number.as_f64()?;
                if value.is_finite() && value > 0.0 && value <= u64::MAX as f64 {
                    Some(value.trunc() as u64)
                } else {
                    Some(0)
                }
            }
        }
        Value::String(value) => value
            .parse::<i128>()
            .ok()
            .map(|value| value.clamp(0, u64::MAX as i128) as u64),
        _ => Some(0),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn matches_python_damage_and_win_examples() {
        let base = compute_offline_rewards(RewardStatistics::default(), false, true, 1);
        let damage = compute_offline_rewards(
            RewardStatistics {
                damage_dealt: 1_000,
                ..RewardStatistics::default()
            },
            false,
            true,
            1,
        );
        let win = compute_offline_rewards(
            RewardStatistics {
                damage_dealt: 1_000,
                ..RewardStatistics::default()
            },
            true,
            true,
            1,
        );
        assert_eq!(
            base,
            OfflineRewards {
                credits: 1_000,
                xp: 100,
                free_xp: 5,
                repair_cost: 0,
                ammo_cost: 0,
            }
        );
        assert_eq!(damage.credits, 3_000);
        assert_eq!(damage.xp, 300);
        assert_eq!(win.credits, 3_850);
        assert_eq!(win.xp, 450);
        assert_eq!(win.free_xp, 22);
    }

    #[test]
    fn kills_award_xp_but_no_separate_credits() {
        let rewards = compute_offline_rewards(
            RewardStatistics {
                kills: 3,
                ..RewardStatistics::default()
            },
            false,
            true,
            1,
        );
        assert_eq!(rewards.credits, 1_000);
        assert_eq!(rewards.xp, 400);
    }

    #[test]
    fn participation_credits_scale_by_tier_and_win() {
        let loss = compute_offline_rewards(RewardStatistics::default(), false, true, 3);
        let win = compute_offline_rewards(RewardStatistics::default(), true, true, 3);
        assert_eq!(loss.credits, 3_000);
        assert_eq!(win.credits, 5_550);
    }

    #[test]
    fn wire_adapter_matches_python_int_and_invalid_fallbacks() {
        let statistics = RewardStatistics::from_wire(&json!({
            "damage_dealt": "12",
            "kills": true,
            "spotted": -4,
            "capture_points": 3.9,
            "damage_assisted_track": null,
            "damage_assisted_radio": [],
        }));
        assert_eq!(statistics.damage_dealt, 12);
        assert_eq!(statistics.kills, 1);
        assert_eq!(statistics.spotted, 0);
        assert_eq!(statistics.capture_points, 3);
        assert_eq!(statistics.damage_assisted_track, 0);
        assert_eq!(statistics.damage_assisted_radio, 0);
    }

    #[test]
    fn costs_are_always_zero_for_offline_battles() {
        let rewards = compute_offline_rewards(
            RewardStatistics {
                damage_dealt: u64::MAX,
                ..RewardStatistics::default()
            },
            true,
            true,
            10,
        );
        assert_eq!((rewards.repair_cost, rewards.ammo_cost), (0, 0));
    }
}
