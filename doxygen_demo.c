/**
 * @file doxygen_demo.c
 * @brief Demo objects for Doxygen agent testing.
 */

#include "doxygen_demo.h"

/** @brief Maximum demo pipes. */
#define DEMO_MAX_PIPES 32

/** @brief Demo pipe directions. */
enum demo_dir {
	DEMO_DIR_UP,
	DEMO_DIR_DOWN,
};

/** @brief Demo point in the grid. */
struct demo_point {
	int row;
	int col;
};

/** @brief Demo value storage. */
union demo_value {
	int i;
	double d;
};

/** @brief Demo identifier type. */
typedef unsigned int demo_id_t;

/** @brief Global demo counter. */
static int demo_counter;

/** @brief Computes demo score from pipe count. */
int demo_score(int pipes)
{
	return pipes * 2;
}
