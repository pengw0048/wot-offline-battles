/*
 * See offline_compute_core.h.  Every statement below mirrors the Python
 * reference in world_collision_prep.py, in its order, so that both produce
 * bit-identical doubles.
 */

#include <math.h>

#include "offline_compute_core.h"


#define LANE_MERGE_EPSILON 1.0e-7
#define POSE_EPSILON 1.0e-9
#define LIMIT_EPSILON 1.0e-9


typedef struct Projection {
	double lane_v;
	double start_u;
	double end_u;
} Projection;


/* Python's ``min``/``max`` keep the first argument unless the second wins. */
static double smaller(double current, double candidate)
{
	return candidate < current ? candidate : current;
}


static double larger(double current, double candidate)
{
	return candidate > current ? candidate : current;
}


/* Python tuple ordering: compare each element, equal elements fall through. */
static int projection_precedes(const Projection *left, const Projection *right)
{
	if (left->lane_v != right->lane_v) {
		return left->lane_v < right->lane_v;
	}
	if (left->start_u != right->start_u) {
		return left->start_u < right->start_u;
	}
	return left->end_u < right->end_u;
}


/* A stable insertion sort reproduces ``sorted`` for these five projections. */
static void sort_projections(Projection *items, int count)
{
	int index;
	for (index = 1; index < count; ++index) {
		Projection current = items[index];
		int scan = index - 1;
		while (scan >= 0 && projection_precedes(&current, &items[scan])) {
			items[scan + 1] = items[scan];
			--scan;
		}
		items[scan + 1] = current;
	}
}


static void hull_pose_endpoint(double start_right, double start_forward,
		double end_right, double end_forward, double half_width,
		double half_length_back, double half_length_front,
		double *out_right, double *out_forward)
{
	double delta_right = end_right - start_right;
	double delta_forward = end_forward - start_forward;
	double fraction = 1.0;
	if (delta_right > 0.0) {
		fraction = smaller(fraction,
			(half_width - start_right) / delta_right);
	} else if (delta_right < 0.0) {
		fraction = smaller(fraction,
			(-half_width - start_right) / delta_right);
	}
	if (delta_forward > 0.0) {
		fraction = smaller(fraction,
			(half_length_front - start_forward) / delta_forward);
	} else if (delta_forward < 0.0) {
		fraction = smaller(fraction,
			(-half_length_back - start_forward) / delta_forward);
	}
	fraction = larger(0.0, smaller(1.0, fraction));
	*out_right = start_right + delta_right * fraction;
	*out_forward = start_forward + delta_forward * fraction;
}


int offline_compute_prepare_sweep(double *values, int count)
{
	static const double LANE_HEIGHTS[3] = {0.6, 1.1, 1.6};
	double lanes[OFFLINE_COMPUTE_MAXIMUM_LANES][OFFLINE_COMPUTE_LANE_VALUES];
	double pos_x, pos_y, pos_z, yaw_sin, yaw_cos;
	double pose_right, pose_up, pose_forward;
	double half_width, half_back, half_front;
	double vel, dt, airborne, motion_sin, motion_cos, has_motion_yaw;
	double ahead, plane_y, gradient_x, gradient_z;
	double minimum_x, maximum_x, minimum_z, maximum_z;
	int posed, lane_count = 0;
	int index, height_index, output;

	if (values == 0 || count < OFFLINE_COMPUTE_BUFFER_VALUES) {
		return OFFLINE_COMPUTE_BUFFER_TOO_SMALL;
	}
	for (index = 0; index < OFFLINE_COMPUTE_INPUT_VALUES; ++index) {
		if (!isfinite(values[index])) {
			return OFFLINE_COMPUTE_INPUT_NOT_FINITE;
		}
	}

	pos_x = values[0];
	pos_y = values[1];
	pos_z = values[2];
	yaw_sin = values[3];
	yaw_cos = values[4];
	pose_right = values[5];
	pose_up = values[6];
	pose_forward = values[7];
	half_width = values[8];
	half_back = values[9];
	half_front = values[10];
	vel = values[11];
	dt = values[12];
	airborne = values[13];
	motion_sin = values[14];
	motion_cos = values[15];
	has_motion_yaw = values[16];

	if (airborne != 0.0) {
		ahead = fabs(vel) * dt + 0.2;
	} else {
		ahead = larger(0.4, fabs(vel) * dt + 0.2);
	}
	plane_y = pos_y + 1.6 * pose_up;
	gradient_x = yaw_cos * pose_right + yaw_sin * pose_forward;
	gradient_z = -yaw_sin * pose_right + yaw_cos * pose_forward;
	posed = !(pose_right == 0.0 && pose_up == 1.0 && pose_forward == 0.0);

	if (has_motion_yaw == 0.0) {
		double back_margin = vel > 0.0 ? -0.5 : 0.5;
		double front_margin = vel > 0.0 ? (half_front + ahead) :
			-(half_back + ahead);
		double direction = vel >= 0.0 ? 1.0 : -1.0;
		double look = (vel > 0.0 ? half_front : half_back) + ahead;
		double target_len = fabs(back_margin) + look;
		double offsets[3];
		offsets[0] = -half_width;
		offsets[1] = 0.0;
		offsets[2] = half_width;
		for (index = 0; index < 3; ++index) {
			double sx = pos_x + yaw_cos * offsets[index];
			double sz = pos_z - yaw_sin * offsets[index];
			double *lane = lanes[lane_count++];
			lane[0] = sx + yaw_sin * back_margin;
			lane[1] = sz + yaw_cos * back_margin;
			lane[2] = sx + yaw_sin * front_margin;
			lane[3] = sz + yaw_cos * front_margin;
			lane[4] = target_len;
			lane[5] = sx;
			lane[6] = sz;
			lane[7] = yaw_sin;
			lane[8] = yaw_cos;
			lane[9] = direction;
			lane[10] = look;
		}
	} else {
		double perp_x = motion_cos;
		double perp_z = -motion_sin;
		double right_u = motion_sin * yaw_cos - motion_cos * yaw_sin;
		double forward_u = motion_sin * yaw_sin + motion_cos * yaw_cos;
		double right_v = perp_x * yaw_cos - perp_z * yaw_sin;
		double forward_v = perp_x * yaw_sin + perp_z * yaw_cos;
		double corners[4][2];
		double center_front = 0.0;
		int has_limit = 0;
		Projection projected[5];
		Projection merged[5];
		int merged_count = 0;
		corners[0][0] = -half_width; corners[0][1] = -half_back;
		corners[1][0] = half_width;  corners[1][1] = -half_back;
		corners[2][0] = half_width;  corners[2][1] = half_front;
		corners[3][0] = -half_width; corners[3][1] = half_front;
		for (index = 0; index < 4; ++index) {
			double corner_u = right_u * corners[index][0] +
				forward_u * corners[index][1];
			double corner_v = right_v * corners[index][0] +
				forward_v * corners[index][1];
			projected[index].lane_v = corner_v;
			projected[index].start_u = corner_u;
			projected[index].end_u = corner_u + ahead;
		}
		if (right_u > LIMIT_EPSILON) {
			center_front = half_width / right_u;
			has_limit = 1;
		} else if (right_u < -LIMIT_EPSILON) {
			center_front = -half_width / right_u;
			has_limit = 1;
		}
		if (forward_u > LIMIT_EPSILON) {
			double limit = half_front / forward_u;
			center_front = has_limit ? smaller(center_front, limit) : limit;
			has_limit = 1;
		} else if (forward_u < -LIMIT_EPSILON) {
			double limit = -half_back / forward_u;
			center_front = has_limit ? smaller(center_front, limit) : limit;
			has_limit = 1;
		}
		if (!has_limit) {
			center_front = 0.0;
		}
		projected[4].lane_v = 0.0;
		projected[4].start_u = -0.5;
		projected[4].end_u = center_front + ahead;
		sort_projections(projected, 5);
		for (index = 0; index < 5; ++index) {
			if (merged_count > 0 &&
					fabs(projected[index].lane_v -
						merged[merged_count - 1].lane_v) <=
					LANE_MERGE_EPSILON) {
				Projection *previous = &merged[merged_count - 1];
				previous->start_u = smaller(
					previous->start_u, projected[index].start_u);
				previous->end_u = larger(
					previous->end_u, projected[index].end_u);
			} else {
				merged[merged_count++] = projected[index];
			}
		}
		if (merged_count > OFFLINE_COMPUTE_MAXIMUM_LANES) {
			return OFFLINE_COMPUTE_LANE_OVERFLOW;
		}
		for (index = 0; index < merged_count; ++index) {
			double lane_v = merged[index].lane_v;
			double start_u = merged[index].start_u;
			double end_u = merged[index].end_u;
			double *lane = lanes[lane_count++];
			lane[0] = pos_x + perp_x * lane_v + motion_sin * start_u;
			lane[1] = pos_z + perp_z * lane_v + motion_cos * start_u;
			lane[2] = pos_x + perp_x * lane_v + motion_sin * end_u;
			lane[3] = pos_z + perp_z * lane_v + motion_cos * end_u;
			lane[4] = end_u - start_u;
			lane[5] = lane[0];
			lane[6] = lane[1];
			lane[7] = motion_sin;
			lane[8] = motion_cos;
			lane[9] = 1.0;
			lane[10] = lane[4];
		}
	}

	if (lane_count < 1 || lane_count > OFFLINE_COMPUTE_MAXIMUM_LANES) {
		return OFFLINE_COMPUTE_LANE_OVERFLOW;
	}
	minimum_x = maximum_x = lanes[0][0];
	minimum_z = maximum_z = lanes[0][1];
	for (index = 0; index < lane_count; ++index) {
		minimum_x = smaller(smaller(minimum_x, lanes[index][0]),
			lanes[index][2]);
		maximum_x = larger(larger(maximum_x, lanes[index][0]),
			lanes[index][2]);
		minimum_z = smaller(smaller(minimum_z, lanes[index][1]),
			lanes[index][3]);
		maximum_z = larger(larger(maximum_z, lanes[index][1]),
			lanes[index][3]);
	}

	for (index = 0; index < lane_count; ++index) {
		double *lane = lanes[index];
		double start_dx = lane[0] - pos_x;
		double start_dz = lane[1] - pos_z;
		double end_dx = lane[2] - pos_x;
		double end_dz = lane[3] - pos_z;
		double local_start_r = start_dx * yaw_cos - start_dz * yaw_sin;
		double local_start_f = start_dx * yaw_sin + start_dz * yaw_cos;
		double ray_end_r = end_dx * yaw_cos - end_dz * yaw_sin;
		double ray_end_f = end_dx * yaw_sin + end_dz * yaw_cos;
		double local_end_r, local_end_f;
		int clamped;
		hull_pose_endpoint(local_start_r, local_start_f, ray_end_r,
			ray_end_f, half_width, half_back, half_front,
			&local_end_r, &local_end_f);
		clamped = posed && (
			fabs(local_end_r - ray_end_r) > POSE_EPSILON ||
			fabs(local_end_f - ray_end_f) > POSE_EPSILON);
		lane[11] = local_start_r;
		lane[12] = local_start_f;
		lane[13] = local_end_r;
		lane[14] = local_end_f;
		lane[15] = clamped ? 1.0 : 0.0;
		lane[16] = pos_x + yaw_cos * local_end_r + yaw_sin * local_end_f;
		lane[17] = pos_z - yaw_sin * local_end_r + yaw_cos * local_end_f;
		for (height_index = 0; height_index < 3; ++height_index) {
			double height = LANE_HEIGHTS[height_index];
			lane[18 + 2 * height_index] = pos_y +
				local_start_r * pose_right + height * pose_up +
				local_start_f * pose_forward;
			lane[19 + 2 * height_index] = pos_y +
				local_end_r * pose_right + height * pose_up +
				local_end_f * pose_forward;
		}
	}

	output = OFFLINE_COMPUTE_INPUT_VALUES;
	values[output] = ahead;
	values[output + 1] = pos_x;
	values[output + 2] = plane_y;
	values[output + 3] = pos_z;
	values[output + 4] = gradient_x;
	values[output + 5] = gradient_z;
	values[output + 6] = minimum_x;
	values[output + 7] = maximum_x;
	values[output + 8] = minimum_z;
	values[output + 9] = maximum_z;
	values[output + 10] = (double)lane_count;
	output += OFFLINE_COMPUTE_HEADER_VALUES;
	for (index = 0; index < lane_count; ++index) {
		for (height_index = 0; height_index < OFFLINE_COMPUTE_LANE_VALUES;
				++height_index) {
			values[output + height_index] = lanes[index][height_index];
		}
		output += OFFLINE_COMPUTE_LANE_VALUES;
	}
	return OFFLINE_COMPUTE_OK;
}
