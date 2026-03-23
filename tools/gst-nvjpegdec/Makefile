CFLAGS += $(shell pkg-config --cflags --libs gstreamer-1.0)

main: main.o
	$(CXX) -o $@ $^ $(CFLAGS)

main.o: main.cpp
	$(CXX) -c $< -o $@ $(CFLAGS)

clean:
	rm -f main main.o