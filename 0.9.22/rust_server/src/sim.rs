use std::collections::BTreeMap;
use std::time::Duration;
use thiserror::Error;

pub const SIM_TICK_HZ: u64 = 30;
pub const MAX_CATCH_UP_TICKS: u64 = 4;
pub const MAX_OVERRUN_TICKS: u64 = 8;

const MICROS_PER_SECOND: u128 = 1_000_000;
const NANOS_PER_SECOND: u128 = 1_000_000_000;

/// Canonical simulation time is derived from the tick number, never from the
/// native oracle's render cadence or from an accumulated floating-point dt.
pub fn time_us_at_tick(tick: u64) -> u64 {
    ((u128::from(tick) * MICROS_PER_SECOND) / u128::from(SIM_TICK_HZ)).min(u128::from(u64::MAX))
        as u64
}

pub fn time_ms_at_tick(tick: u64) -> u64 {
    time_us_at_tick(tick) / 1_000
}

pub fn delta_us_for_tick(tick: u64) -> u64 {
    time_us_at_tick(tick).saturating_sub(time_us_at_tick(tick.saturating_sub(1)))
}

pub fn scheduled_tick_at(elapsed: Duration) -> u64 {
    // `clock::tick_offset()` floors each absolute deadline. Invert that
    // exact schedule instead of flooring `elapsed * Hz`, which would make
    // ticks 1 and 2 appear one nanosecond late.
    (((elapsed.as_nanos().saturating_add(1) * u128::from(SIM_TICK_HZ)).saturating_sub(1))
        / NANOS_PER_SECOND)
        .min(u128::from(u64::MAX)) as u64
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TickStep {
    pub tick: u64,
    pub time_us: u64,
    pub dt_us: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TickBatch {
    pub first_tick: u64,
    pub steps: u64,
    pub remaining_lag: u64,
}

impl TickBatch {
    pub fn iter(self) -> impl Iterator<Item = TickStep> {
        let end = self.first_tick.saturating_add(self.steps);
        (self.first_tick..end).map(|tick| TickStep {
            tick,
            time_us: time_us_at_tick(tick),
            dt_us: delta_us_for_tick(tick),
        })
    }
}

#[derive(Clone, Copy, Debug, Error, PartialEq, Eq)]
pub enum TickError {
    #[error("simulation fell {lag_ticks} ticks behind its fixed schedule")]
    SimulationOverrun { lag_ticks: u64 },
}

/// Pure catch-up policy for the live simulation thread.
///
/// A poll never skips logical ticks and never stretches dt. It yields at most
/// four consecutive ticks so networking can run between catch-up batches. A
/// lag above eight ticks is terminal for the active round.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct TickController {
    completed_tick: u64,
}

impl TickController {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn starting_after(completed_tick: u64) -> Self {
        Self { completed_tick }
    }

    pub fn completed_tick(&self) -> u64 {
        self.completed_tick
    }

    pub fn poll(&mut self, elapsed: Duration) -> Result<Option<TickBatch>, TickError> {
        self.poll_with_limit(elapsed, MAX_CATCH_UP_TICKS)
    }

    /// Yield no more than `limit` consecutive ticks from the current lag.
    ///
    /// The native-oracle battle loop uses a limit of one so every T+3 query is
    /// put on the socket before another logical tick can consume or time it
    /// out. Other pure-data users retain the normal four-tick catch-up batch.
    pub fn poll_with_limit(
        &mut self,
        elapsed: Duration,
        limit: u64,
    ) -> Result<Option<TickBatch>, TickError> {
        let scheduled = scheduled_tick_at(elapsed);
        let lag = scheduled.saturating_sub(self.completed_tick);
        if lag == 0 {
            return Ok(None);
        }
        if lag > MAX_OVERRUN_TICKS {
            return Err(TickError::SimulationOverrun { lag_ticks: lag });
        }

        let steps = lag.min(limit.clamp(1, MAX_CATCH_UP_TICKS));
        let first_tick = self.completed_tick.saturating_add(1);
        self.completed_tick = self.completed_tick.saturating_add(steps);
        Ok(Some(TickBatch {
            first_tick,
            steps,
            remaining_lag: lag - steps,
        }))
    }
}

#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum IngressError {
    #[error("receive sequence {recv_seq} was already queued")]
    Duplicate { recv_seq: u64 },
    #[error("receive sequence {recv_seq} arrived after sequence {applied_through} was applied")]
    Stale { recv_seq: u64, applied_through: u64 },
}

/// Tick-boundary mailbox ordered by the process-global receive sequence.
#[derive(Clone, Debug)]
pub struct IngressQueue<T> {
    pending: BTreeMap<u64, T>,
    applied_through: u64,
}

impl<T> Default for IngressQueue<T> {
    fn default() -> Self {
        Self {
            pending: BTreeMap::new(),
            applied_through: 0,
        }
    }
}

impl<T> IngressQueue<T> {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn push(&mut self, recv_seq: u64, value: T) -> Result<(), IngressError> {
        if recv_seq <= self.applied_through {
            return Err(IngressError::Stale {
                recv_seq,
                applied_through: self.applied_through,
            });
        }
        if self.pending.contains_key(&recv_seq) {
            return Err(IngressError::Duplicate { recv_seq });
        }
        self.pending.insert(recv_seq, value);
        Ok(())
    }

    pub fn len(&self) -> usize {
        self.pending.len()
    }

    pub fn is_empty(&self) -> bool {
        self.pending.is_empty()
    }

    /// Drains all messages visible at this tick boundary in receive order.
    /// The caller must assign recv_seq and enqueue atomically; gaps therefore
    /// mean no message exists for the missing sequence, not delayed delivery.
    pub fn drain_ordered(&mut self) -> Vec<(u64, T)> {
        let pending = std::mem::take(&mut self.pending);
        let mut drained = Vec::with_capacity(pending.len());
        for (recv_seq, value) in pending {
            self.applied_through = recv_seq;
            drained.push((recv_seq, value));
        }
        drained
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ten_seconds_are_exactly_three_hundred_ticks() {
        assert_eq!(scheduled_tick_at(Duration::from_secs(10)), 300);
        assert_eq!(time_us_at_tick(300), 10_000_000);
    }

    #[test]
    fn inverse_schedule_matches_the_absolute_clock_deadlines() {
        assert_eq!(scheduled_tick_at(Duration::from_nanos(33_333_332)), 0);
        assert_eq!(scheduled_tick_at(Duration::from_nanos(33_333_333)), 1);
        assert_eq!(scheduled_tick_at(Duration::from_nanos(66_666_665)), 1);
        assert_eq!(scheduled_tick_at(Duration::from_nanos(66_666_666)), 2);
        assert_eq!(scheduled_tick_at(Duration::from_millis(100)), 3);
    }

    #[test]
    fn integer_dt_preserves_the_one_second_boundary() {
        let deltas: Vec<_> = (1..=30).map(delta_us_for_tick).collect();
        assert_eq!(
            &deltas[..6],
            &[33_333, 33_333, 33_334, 33_333, 33_333, 33_334]
        );
        assert_eq!(deltas.iter().sum::<u64>(), 1_000_000);
    }

    #[test]
    fn controller_catches_up_without_skipping_ticks() {
        let mut controller = TickController::new();
        let first = controller
            .poll(Duration::from_millis(200))
            .unwrap()
            .unwrap();
        assert_eq!(first.steps, 4);
        assert_eq!(first.remaining_lag, 2);
        assert_eq!(
            first.iter().map(|step| step.tick).collect::<Vec<_>>(),
            vec![1, 2, 3, 4]
        );

        let second = controller
            .poll(Duration::from_millis(200))
            .unwrap()
            .unwrap();
        assert_eq!(
            second.iter().map(|step| step.tick).collect::<Vec<_>>(),
            vec![5, 6]
        );
        assert_eq!(controller.completed_tick(), 6);
    }

    #[test]
    fn sustained_lag_is_terminal_instead_of_resetting_time() {
        let mut controller = TickController::new();
        assert_eq!(
            controller.poll(Duration::from_secs(1)),
            Err(TickError::SimulationOverrun { lag_ticks: 30 })
        );
        assert_eq!(controller.completed_tick(), 0);
    }

    #[test]
    fn ingress_is_deterministic_at_tick_boundaries() {
        let mut queue = IngressQueue::new();
        queue.push(3, "third").unwrap();
        queue.push(1, "first").unwrap();
        queue.push(2, "second").unwrap();
        assert_eq!(
            queue.drain_ordered(),
            vec![(1, "first"), (2, "second"), (3, "third")]
        );
        assert_eq!(
            queue.push(2, "late"),
            Err(IngressError::Stale {
                recv_seq: 2,
                applied_through: 3,
            })
        );
    }

    #[test]
    fn duplicate_ingress_is_rejected_without_replacing_the_original() {
        let mut queue = IngressQueue::new();
        queue.push(7, "original").unwrap();
        assert_eq!(
            queue.push(7, "replacement"),
            Err(IngressError::Duplicate { recv_seq: 7 })
        );
        assert_eq!(queue.drain_ordered(), vec![(7, "original")]);
    }
}
