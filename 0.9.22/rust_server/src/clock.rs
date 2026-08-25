use serde::{Deserialize, Serialize};
use std::thread;
use std::time::{Duration, Instant};
use thiserror::Error;

pub const TICK_RATE_HZ: u64 = 30;
const NANOS_PER_SECOND: u64 = 1_000_000_000;

/// Returns the absolute offset of a simulation tick from the clock anchor.
///
/// Computing every deadline from the anchor avoids accumulating the rounded
/// remainder of 1 / 30 second or the oversleep of a previous tick.
pub fn tick_offset(tick: u64) -> Duration {
    let seconds = tick / TICK_RATE_HZ;
    let remainder_ticks = tick % TICK_RATE_HZ;
    let nanos = remainder_ticks * NANOS_PER_SECOND / TICK_RATE_HZ;
    Duration::new(seconds, nanos as u32)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TickSlot {
    pub tick: u64,
    pub offset: Duration,
}

/// A pure fixed-step schedule. It is separate from sleeping so simulation and
/// replay code can share the exact same tick numbering deterministically.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FixedStepSchedule {
    next_tick: u64,
}

impl Default for FixedStepSchedule {
    fn default() -> Self {
        Self::new()
    }
}

impl FixedStepSchedule {
    pub fn new() -> Self {
        Self { next_tick: 1 }
    }

    pub fn starting_after(last_tick: u64) -> Self {
        Self {
            next_tick: last_tick.saturating_add(1),
        }
    }

    pub fn next_tick(&self) -> u64 {
        self.next_tick
    }
}

impl Iterator for FixedStepSchedule {
    type Item = TickSlot;

    fn next(&mut self) -> Option<Self::Item> {
        if self.next_tick == u64::MAX {
            return None;
        }
        let tick = self.next_tick;
        self.next_tick += 1;
        Some(TickSlot {
            tick,
            offset: tick_offset(tick),
        })
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct TickObservation {
    pub tick: u64,
    pub scheduled_ns: u128,
    pub observed_ns: u128,
    pub lateness_ns: u128,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ClockError {
    #[error("the fixed-step tick counter is exhausted")]
    TickCounterExhausted,
    #[error("the fixed-step deadline exceeds the monotonic clock range")]
    DeadlineOverflow,
}

/// A monotonic 30 Hz wall-clock driver for the live authority process.
pub struct FixedStepClock {
    anchor: Instant,
    schedule: FixedStepSchedule,
}

impl Default for FixedStepClock {
    fn default() -> Self {
        Self::new()
    }
}

impl FixedStepClock {
    pub fn new() -> Self {
        Self {
            anchor: Instant::now(),
            schedule: FixedStepSchedule::new(),
        }
    }

    pub fn next_tick(&self) -> u64 {
        self.schedule.next_tick()
    }

    pub fn wait_next(&mut self) -> Result<TickObservation, ClockError> {
        let slot = self
            .schedule
            .next()
            .ok_or(ClockError::TickCounterExhausted)?;
        let deadline = self
            .anchor
            .checked_add(slot.offset)
            .ok_or(ClockError::DeadlineOverflow)?;

        let before_sleep = Instant::now();
        if before_sleep < deadline {
            thread::sleep(deadline.duration_since(before_sleep));
        }

        let observed = Instant::now().duration_since(self.anchor);
        let scheduled_ns = slot.offset.as_nanos();
        let observed_ns = observed.as_nanos();
        Ok(TickObservation {
            tick: slot.tick,
            scheduled_ns,
            observed_ns,
            lateness_ns: observed_ns.saturating_sub(scheduled_ns),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn thirty_ticks_land_on_exactly_one_second() {
        assert_eq!(tick_offset(30), Duration::from_secs(1));
        assert_eq!(tick_offset(300), Duration::from_secs(10));
    }

    #[test]
    fn fractional_nanoseconds_do_not_accumulate_drift() {
        assert_eq!(tick_offset(1).as_nanos(), 33_333_333);
        assert_eq!(tick_offset(2).as_nanos(), 66_666_666);
        assert_eq!(tick_offset(3).as_nanos(), 100_000_000);
        assert_eq!(tick_offset(29).as_nanos(), 966_666_666);
        assert_eq!(tick_offset(30).as_nanos(), 1_000_000_000);
    }

    #[test]
    fn schedule_starts_at_tick_one_and_is_monotonic() {
        let mut schedule = FixedStepSchedule::new();
        let first = schedule.next().unwrap();
        let second = schedule.next().unwrap();
        assert_eq!(first.tick, 1);
        assert_eq!(second.tick, 2);
        assert!(second.offset > first.offset);
        assert_eq!(schedule.next_tick(), 3);
    }

    #[test]
    fn schedule_can_resume_after_a_replayed_tick() {
        let mut schedule = FixedStepSchedule::starting_after(99);
        assert_eq!(schedule.next().unwrap().tick, 100);
    }
}
