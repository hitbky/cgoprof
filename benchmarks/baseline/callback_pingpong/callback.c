extern void goCallback(int);

void call_back_many(int n) {
	for (int i = 0; i < n; i++) {
		goCallback(i);
	}
}
