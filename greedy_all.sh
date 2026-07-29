for v in p3.6 p4.0 p4.4 p4.8 p5.2 p6.0; do
  python greedy.py $v 2>/dev/null | grep RESULT_JSON
done
