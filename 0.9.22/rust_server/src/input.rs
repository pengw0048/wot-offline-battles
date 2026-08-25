use serde_json::Value;
use std::collections::{BTreeMap, VecDeque};
use thiserror::Error;

pub const MAX_INPUT_SEQUENCE: u64 = 2_147_483_647;
pub const MAX_INPUT_FINGERPRINTS: usize = 128;
pub const POSE_HISTORY_US: u64 = 1_500_000;
pub const POSE_STALE_RESET_US: u64 = 3_000_000;
pub const POSE_MAX_SAMPLE_GAP_US: u64 = 250_000;
pub const POSE_CLOCK_LEEWAY_US: u64 = 250_000;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PoseState {
    pub x: f64,
    pub y: f64,
    pub z: f64,
    pub yaw: f64,
    pub speed: f64,
    pub ram_vx: f64,
    pub ram_vy: f64,
    pub ram_vz: f64,
    pub alive: bool,
}

impl PoseState {
    fn finite(self) -> bool {
        [
            self.x,
            self.y,
            self.z,
            self.yaw,
            self.speed,
            self.ram_vx,
            self.ram_vy,
            self.ram_vz,
        ]
        .into_iter()
        .all(f64::is_finite)
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PoseSample {
    pub input_seq: u64,
    pub time_us: u64,
    pub x: f64,
    pub y: f64,
    pub z: f64,
    pub yaw: f64,
    pub vx: f64,
    pub vy: f64,
    pub vz: f64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InputAdmission {
    Accepted,
    ExactRetry,
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum InputError {
    #[error("input is not a JSON object")]
    NotObject,
    #[error("input_seq must be an integer in 1..=2147483647")]
    InvalidSequence,
    #[error("input sequence {received} does not follow {last}")]
    SequenceGap { last: u64, received: u64 },
    #[error("input sequence {sequence} was reused with different content")]
    ConflictingRetry { sequence: u64 },
    #[error("input could not be serialized canonically")]
    Fingerprint,
}

#[derive(Clone, Debug, Default)]
pub struct InputTimeline {
    last_input_seq: u64,
    fingerprints: BTreeMap<u64, String>,
    pose_time_us: Option<u64>,
    poses: VecDeque<PoseSample>,
}

impl InputTimeline {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn last_input_seq(&self) -> u64 {
        self.last_input_seq
    }

    pub fn pose_time_us(&self) -> Option<u64> {
        self.pose_time_us
    }

    pub fn poses(&self) -> &VecDeque<PoseSample> {
        &self.poses
    }

    /// Admits an ordered input transaction. An exact retransmission is an
    /// idempotent success; a gap, rewind, or conflicting retry is rejected.
    pub fn admit(&mut self, message: &Value) -> Result<InputAdmission, InputError> {
        let object = message.as_object().ok_or(InputError::NotObject)?;
        let sequence = object
            .get("input_seq")
            .and_then(Value::as_u64)
            .filter(|value| (1..=MAX_INPUT_SEQUENCE).contains(value))
            .ok_or(InputError::InvalidSequence)?;
        let fingerprint = serde_json::to_string(message).map_err(|_| InputError::Fingerprint)?;

        if let Some(previous) = self.fingerprints.get(&sequence) {
            return if previous == &fingerprint {
                Ok(InputAdmission::ExactRetry)
            } else {
                Err(InputError::ConflictingRetry { sequence })
            };
        }
        if sequence != self.last_input_seq.saturating_add(1) {
            return Err(InputError::SequenceGap {
                last: self.last_input_seq,
                received: sequence,
            });
        }

        self.last_input_seq = sequence;
        self.fingerprints.insert(sequence, fingerprint);
        while self.fingerprints.len() > MAX_INPUT_FINGERPRINTS {
            if let Some(oldest) = self.fingerprints.keys().next().copied() {
                self.fingerprints.remove(&oldest);
            }
        }
        Ok(InputAdmission::Accepted)
    }

    /// Records the admitted input's source-timed body pose. Invalid clocks
    /// clear interpolation history so no collision can extrapolate across a
    /// discontinuity.
    pub fn record_pose(
        &mut self,
        source_time_us: u64,
        receipt_time_us: u64,
        pose: PoseState,
    ) -> bool {
        if !pose.finite()
            || source_time_us > receipt_time_us.saturating_add(POSE_CLOCK_LEEWAY_US)
            || source_time_us < receipt_time_us.saturating_sub(POSE_STALE_RESET_US)
        {
            self.clear_pose_history();
            return false;
        }

        let sample_time_us = source_time_us.min(receipt_time_us);
        if self.pose_time_us.is_some_and(|previous| {
            sample_time_us <= previous
                || sample_time_us.saturating_sub(previous) > POSE_MAX_SAMPLE_GAP_US
        }) {
            self.poses.clear();
        }

        let (vx, vy, vz) = if pose.alive {
            (pose.ram_vx, pose.ram_vy, pose.ram_vz)
        } else {
            (0.0, 0.0, 0.0)
        };
        self.poses.push_back(PoseSample {
            input_seq: self.last_input_seq,
            time_us: sample_time_us,
            x: pose.x,
            y: pose.y,
            z: pose.z,
            yaw: pose.yaw,
            vx,
            vy,
            vz,
        });
        self.pose_time_us = Some(sample_time_us);

        let oldest = sample_time_us.saturating_sub(POSE_HISTORY_US);
        while self.poses.len() > 2
            && self
                .poses
                .get(1)
                .is_some_and(|sample| sample.time_us < oldest)
        {
            self.poses.pop_front();
        }
        true
    }

    pub fn interpolate_pose(&self, frontier_time_us: u64) -> Option<PoseSample> {
        if self.poses.len() < 2 {
            return None;
        }

        let left = self
            .poses
            .iter()
            .rev()
            .find(|sample| sample.time_us <= frontier_time_us)?;
        let right = self
            .poses
            .iter()
            .find(|sample| sample.time_us >= frontier_time_us)?;
        let span = right.time_us.saturating_sub(left.time_us);
        if span > POSE_MAX_SAMPLE_GAP_US {
            return None;
        }
        let ratio = if span == 0 {
            0.0
        } else {
            (frontier_time_us.saturating_sub(left.time_us) as f64) / (span as f64)
        };
        let yaw_delta = wrap_angle(right.yaw - left.yaw);
        Some(PoseSample {
            input_seq: right.input_seq,
            time_us: frontier_time_us,
            x: lerp(left.x, right.x, ratio),
            y: lerp(left.y, right.y, ratio),
            z: lerp(left.z, right.z, ratio),
            yaw: left.yaw + yaw_delta * ratio,
            vx: lerp(left.vx, right.vx, ratio),
            vy: lerp(left.vy, right.vy, ratio),
            vz: lerp(left.vz, right.vz, ratio),
        })
    }

    pub fn clear_pose_history(&mut self) {
        self.poses.clear();
        self.pose_time_us = None;
    }
}

fn lerp(left: f64, right: f64, ratio: f64) -> f64 {
    left + (right - left) * ratio
}

fn wrap_angle(value: f64) -> f64 {
    let full = std::f64::consts::TAU;
    (value + std::f64::consts::PI).rem_euclid(full) - std::f64::consts::PI
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn pose(x: f64, yaw: f64) -> PoseState {
        PoseState {
            x,
            y: 2.0,
            z: 3.0,
            yaw,
            speed: 10.0,
            ram_vx: 1.0,
            ram_vy: 2.0,
            ram_vz: 3.0,
            alive: true,
        }
    }

    #[test]
    fn ordered_input_and_exact_retry_are_idempotent() {
        let mut timeline = InputTimeline::new();
        let first = json!({"type":"input","input_seq":1,"forward":1.0});
        assert_eq!(timeline.admit(&first), Ok(InputAdmission::Accepted));
        assert_eq!(timeline.admit(&first), Ok(InputAdmission::ExactRetry));
        assert_eq!(timeline.last_input_seq(), 1);
    }

    #[test]
    fn gap_and_conflicting_retry_are_rejected() {
        let mut timeline = InputTimeline::new();
        timeline
            .admit(&json!({"type":"input","input_seq":1}))
            .unwrap();
        assert_eq!(
            timeline.admit(&json!({"type":"input","input_seq":3})),
            Err(InputError::SequenceGap {
                last: 1,
                received: 3
            })
        );
        assert_eq!(
            timeline.admit(&json!({"type":"input","input_seq":1,"turn":1})),
            Err(InputError::ConflictingRetry { sequence: 1 })
        );
    }

    #[test]
    fn pose_interpolates_without_extrapolating() {
        let mut timeline = InputTimeline::new();
        timeline.admit(&json!({"input_seq":1})).unwrap();
        assert!(timeline.record_pose(1_000_000, 1_000_000, pose(0.0, 0.0)));
        timeline.admit(&json!({"input_seq":2})).unwrap();
        assert!(timeline.record_pose(1_100_000, 1_100_000, pose(10.0, 0.0)));
        assert_eq!(timeline.interpolate_pose(1_050_000).unwrap().x, 5.0);
        assert!(timeline.interpolate_pose(999_999).is_none());
        assert!(timeline.interpolate_pose(1_100_001).is_none());
    }

    #[test]
    fn yaw_uses_the_short_path_across_pi() {
        let mut timeline = InputTimeline::new();
        timeline.admit(&json!({"input_seq":1})).unwrap();
        timeline
            .record_pose(1_000_000, 1_000_000, pose(0.0, 3.0))
            .then_some(())
            .unwrap();
        timeline.admit(&json!({"input_seq":2})).unwrap();
        timeline
            .record_pose(1_100_000, 1_100_000, pose(0.0, -3.0))
            .then_some(())
            .unwrap();
        let midpoint = timeline.interpolate_pose(1_050_000).unwrap();
        assert!(midpoint.yaw.abs() > 3.0);
    }

    #[test]
    fn broken_source_clock_clears_history() {
        let mut timeline = InputTimeline::new();
        timeline.admit(&json!({"input_seq":1})).unwrap();
        assert!(timeline.record_pose(1_000_000, 1_000_000, pose(0.0, 0.0)));
        assert!(!timeline.record_pose(2_000_000, 1_000_000, pose(1.0, 0.0)));
        assert!(timeline.poses().is_empty());
        assert_eq!(timeline.pose_time_us(), None);
    }

    #[test]
    fn dead_pose_has_zero_velocity() {
        let mut timeline = InputTimeline::new();
        timeline.admit(&json!({"input_seq":1})).unwrap();
        let mut dead = pose(0.0, 1.0);
        dead.alive = false;
        assert!(timeline.record_pose(1_000, 1_000, dead));
        let sample = timeline.poses().back().unwrap();
        assert_eq!((sample.vx, sample.vy, sample.vz), (0.0, 0.0, 0.0));
    }
}
