#include <stddef.h>

int bare_helper(int value)
{
	return value + 1;
}

size_t bare_strlen(const char *text)
{
	size_t length = 0;

	while (text[length] != '\0') {
		++length;
	}
	return length;
}
