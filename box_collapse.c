#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/**
 * @brief Generates a matrix of connected pipes using Wave Function Collapse.
 *
 * Each tile is represented by a four-bit connection mask. Constraint
 * propagation enforces matching edges between adjacent tiles.
 *
 * @param n Number of rows and columns in the output matrix.
 * @param pct Approximate percentage of matrix cells occupied by pipes.
 * @param seed Pseudorandom number generator seed.
 * @return A dynamically allocated `n` by `n` matrix of UTF-8 strings, or
 *         `NULL` on failure. The caller owns the returned matrix and strings.
 */
char ***wfc(int n, int pct, unsigned int seed) {
    enum { UP = 1, DOWN = 2, LEFT = 4, RIGHT = 8 };

    static const char *const symbols[16] = {
        " ", "║", "║", "║",
        "═", "╝", "╗", "╣",
        "═", "╚", "╔", "╠",
        "═", "╩", "╦", "╬"
    };
    static const int dr[4] = {-1, 1, 0, 0};
    static const int dc[4] = {0, 0, -1, 1};
    static const int side[4] = {UP, DOWN, LEFT, RIGHT};
    static const int opposite[4] = {DOWN, UP, RIGHT, LEFT};

    if (n < 1 || n > 500 || pct < 0 || pct > 100) {
        fprintf(stderr, "WFC: invalid matrix size or pipe percentage\n");
        return NULL;
    }
    srand(seed);

    const int cell_count = n * n;

    uint16_t *wave = malloc((size_t)cell_count * sizeof(*wave));
    int *queue = malloc((size_t)cell_count * sizeof(*queue));
    unsigned char *queued = calloc((size_t)cell_count, sizeof(*queued));
    if (wave == NULL || queue == NULL || queued == NULL) {
        fprintf(stderr, "brak pamieci\n");
        free(queued);
        free(queue);
        free(wave);
        return NULL;
    }

    uint16_t initial = 0;
    for (int tile = 0; tile < 16; ++tile) {
        int bits = 0;
        for (int value = tile; value != 0; value >>= 1)
            bits += value & 1;

        if ((tile == 0 && pct < 100) || (bits >= 2 && pct > 0))
            initial |= (uint16_t)(1u << tile);
    }

    int solved = 0;
    for (int attempt = 0; attempt < 100 && !solved; ++attempt) {
        for (int index = 0; index < cell_count; ++index)
            wave[index] = initial;

        for (int row = 0; row < n; ++row) {
            for (int col = 0; col < n; ++col) {
                int index = row * n + col;
                uint16_t options = wave[index];

                for (int tile = 0; tile < 16; ++tile) {
                    if (!(options & (uint16_t)(1u << tile)))
                        continue;
                    if ((row == 0 && (tile & UP)) ||
                        (row == n - 1 && (tile & DOWN)) ||
                        (col == 0 && (tile & LEFT)) ||
                        (col == n - 1 && (tile & RIGHT)))
                        wave[index] &= (uint16_t)~(1u << tile);
                }
            }
        }

        int contradiction = 0;
        for (int index = 0; index < cell_count; ++index) {
            if (wave[index] == 0) {
                contradiction = 1;
                break;
            }
        }

        int queue_begin = 0;
        int queue_end = 0;
        int queue_size = 0;
        for (int index = 0; index < cell_count && !contradiction; ++index) {
            queue[queue_end] = index;
            queue_end = (queue_end + 1) % cell_count;
            ++queue_size;
            queued[index] = 1;
        }

        while (queue_size > 0 && !contradiction) {
            int current = queue[queue_begin];
            queue_begin = (queue_begin + 1) % cell_count;
            --queue_size;
            queued[current] = 0;

            int row = current / n;
            int col = current % n;
            for (int direction = 0; direction < 4; ++direction) {
                int next_row = row + dr[direction];
                int next_col = col + dc[direction];
                if (next_row < 0 || next_row >= n ||
                    next_col < 0 || next_col >= n)
                    continue;

                int next = next_row * n + next_col;
                uint16_t old_options = wave[next];
                uint16_t new_options = 0;

                for (int next_tile = 0; next_tile < 16; ++next_tile) {
                    if (!(old_options & (uint16_t)(1u << next_tile)))
                        continue;

                    int compatible = 0;
                    for (int current_tile = 0; current_tile < 16; ++current_tile) {
                        if (!(wave[current] & (uint16_t)(1u << current_tile)))
                            continue;
                        int exits = (current_tile & side[direction]) != 0;
                        int enters = (next_tile & opposite[direction]) != 0;
                        if (exits == enters) {
                            compatible = 1;
                            break;
                        }
                    }

                    if (compatible)
                        new_options |= (uint16_t)(1u << next_tile);
                }

                if (new_options == 0) {
                    contradiction = 1;
                    break;
                }

                if (new_options != old_options) {
                    wave[next] = new_options;
                    if (!queued[next]) {
                        queue[queue_end] = next;
                        queue_end = (queue_end + 1) % cell_count;
                        ++queue_size;
                        queued[next] = 1;
                    }
                }
            }
        }

        while (!contradiction) {
            int minimum = 17;
            int chosen_cell = -1;
            int ties = 0;

            for (int index = 0; index < cell_count; ++index) {
                int count = 0;
                for (int tile = 0; tile < 16; ++tile)
                    count += (wave[index] >> tile) & 1u;

                if (count > 1 && count < minimum) {
                    minimum = count;
                    chosen_cell = index;
                    ties = 1;
                } else if (count > 1 && count == minimum) {
                    ++ties;
                    if (rand() % ties == 0)
                        chosen_cell = index;
                }
            }

            if (chosen_cell < 0) {
                solved = 1;
                break;
            }

            int pipe_tiles = 0;
            for (int tile = 1; tile < 16; ++tile)
                pipe_tiles += (wave[chosen_cell] >> tile) & 1u;

            int total_weight = 0;
            for (int tile = 0; tile < 16; ++tile) {
                if (!(wave[chosen_cell] & (uint16_t)(1u << tile)))
                    continue;
                total_weight += tile == 0
                    ? (100 - pct) * (pipe_tiles > 0 ? pipe_tiles : 1)
                    : pct;
            }

            int pick = total_weight > 0 ? rand() % total_weight : 0;
            int selected_tile = -1;
            for (int tile = 0; tile < 16; ++tile) {
                if (!(wave[chosen_cell] & (uint16_t)(1u << tile)))
                    continue;
                int weight = tile == 0
                    ? (100 - pct) * (pipe_tiles > 0 ? pipe_tiles : 1)
                    : pct;
                if (pick < weight) {
                    selected_tile = tile;
                    break;
                }
                pick -= weight;
            }

            if (selected_tile < 0) {
                contradiction = 1;
                break;
            }
            wave[chosen_cell] = (uint16_t)(1u << selected_tile);

            queue_begin = 0;
            queue_end = 1 % cell_count;
            queue_size = 1;
            queue[0] = chosen_cell;
            queued[chosen_cell] = 1;

            while (queue_size > 0 && !contradiction) {
                int current = queue[queue_begin];
                queue_begin = (queue_begin + 1) % cell_count;
                --queue_size;
                queued[current] = 0;

                int row = current / n;
                int col = current % n;
                for (int direction = 0; direction < 4; ++direction) {
                    int next_row = row + dr[direction];
                    int next_col = col + dc[direction];
                    if (next_row < 0 || next_row >= n ||
                        next_col < 0 || next_col >= n)
                        continue;

                    int next = next_row * n + next_col;
                    uint16_t old_options = wave[next];
                    uint16_t new_options = 0;

                    for (int next_tile = 0; next_tile < 16; ++next_tile) {
                        if (!(old_options & (uint16_t)(1u << next_tile)))
                            continue;

                        int compatible = 0;
                        for (int current_tile = 0; current_tile < 16;
                             ++current_tile) {
                            if (!(wave[current] &
                                  (uint16_t)(1u << current_tile)))
                                continue;
                            int exits =
                                (current_tile & side[direction]) != 0;
                            int enters =
                                (next_tile & opposite[direction]) != 0;
                            if (exits == enters) {
                                compatible = 1;
                                break;
                            }
                        }

                        if (compatible)
                            new_options |= (uint16_t)(1u << next_tile);
                    }

                    if (new_options == 0) {
                        contradiction = 1;
                        break;
                    }

                    if (new_options != old_options) {
                        wave[next] = new_options;
                        if (!queued[next]) {
                            queue[queue_end] = next;
                            queue_end = (queue_end + 1) % cell_count;
                            ++queue_size;
                            queued[next] = 1;
                        }
                    }
                }
            }
        }

        if (!solved) {
            for (int index = 0; index < cell_count; ++index)
                queued[index] = 0;
        }
    }

    if (!solved) {
        fprintf(stderr, "WFC: nie znaleziono rozwiazania po 100 probach\n");
        free(queued);
        free(queue);
        free(wave);
        return NULL;
    }

    char ***matrix = calloc((size_t)n, sizeof(*matrix));
    if (matrix == NULL) {
        free(queued);
        free(queue);
        free(wave);
        return NULL;
    }

    for (int row = 0; row < n; ++row) {
        matrix[row] = calloc((size_t)n, sizeof(*matrix[row]));
        if (matrix[row] == NULL) {
            for (int i = 0; i < row; ++i) {
                for (int col = 0; col < n; ++col)
                    free(matrix[i][col]);
                free(matrix[i]);
            }
            free(matrix);
            free(queued);
            free(queue);
            free(wave);
            return NULL;
        }

        for (int col = 0; col < n; ++col) {
            uint16_t option = wave[row * n + col];
            int tile = 0;
            while (tile < 16 && option != (uint16_t)(1u << tile))
                ++tile;
            const char *symbol = tile < 16 ? symbols[tile] : "?";
            size_t length = strlen(symbol) + 1;
            matrix[row][col] = malloc(length);
            if (matrix[row][col] == NULL) {
                for (int i = 0; i <= row; ++i) {
                    int columns = i == row ? col : n;
                    for (int j = 0; j < columns; ++j)
                        free(matrix[i][j]);
                    free(matrix[i]);
                }
                free(matrix);
                free(queued);
                free(queue);
                free(wave);
                return NULL;
            }
            memcpy(matrix[row][col], symbol, length);
        }
    }

    free(queued);
    free(queue);
    free(wave);
    return matrix;
}

/**
 * @brief Parses command-line arguments and prints a WFC pipe matrix.
 *
 * @param argc Number of command-line arguments.
 * @param argv Argument array: `<n> <pipe_percentage> [seed]`.
 * @return `EXIT_SUCCESS` on success; otherwise, `EXIT_FAILURE`.
 */
int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s <n> <pipe_percentage> [seed]\n", argv[0]);
        return EXIT_FAILURE;
    }

    char *end = NULL;
    long parsed_n = strtol(argv[1], &end, 10);
    if (*argv[1] == '\0' || *end != '\0' || parsed_n < 1 || parsed_n > 500) {
        fprintf(stderr, "n must be in the range 1-500\n");
        return EXIT_FAILURE;
    }

    end = NULL;
    long parsed_pct = strtol(argv[2], &end, 10);
    if (*argv[2] == '\0' || *end != '\0' || parsed_pct < 0 || parsed_pct > 100) {
        fprintf(stderr, "pipe_percentage must be in the range 0-100\n");
        return EXIT_FAILURE;
    }

    unsigned int seed = (unsigned int)time(NULL);
    if (argc >= 4) {
        end = NULL;
        unsigned long parsed_seed = strtoul(argv[3], &end, 10);
        if (*argv[3] == '\0' || *end != '\0') {
            fprintf(stderr, "seed must be a non-negative integer\n");
            return EXIT_FAILURE;
        }
        seed = (unsigned int)parsed_seed;
    }

    int n = (int)parsed_n;
    char ***matrix = wfc(n, (int)parsed_pct, seed);
    if (matrix == NULL)
        return EXIT_FAILURE;

    for (int row = 0; row < n; ++row) {
        for (int col = 0; col < n; ++col)
            fputs(matrix[row][col], stdout);
        putchar('\n');
    }

    for (int row = 0; row < n; ++row) {
        for (int col = 0; col < n; ++col)
            free(matrix[row][col]);
        free(matrix[row]);
    }
    free(matrix);
    return EXIT_SUCCESS;
}

