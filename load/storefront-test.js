import http from 'k6/http';
import { check } from 'k6';

export const options = {
  stages: [
    { duration: '2m', target: 50 },   // ramp up
    { duration: '5m', target: 50 },   // hold - this is where the DB problem will surface
    { duration: '2m', target: 0 },    // ramp down - watch consolidation
  ],
  thresholds: {
    http_req_duration: ['p(95)<800'],   // fails the run if p95 exceeds 800ms
    http_req_failed:   ['rate<0.01'],   // fails the run if error rate exceeds 1%
  },
};

export default function () {
  const res = http.get(`http://${__ENV.ALB}/`);
  check(res, { 'status is 200': (r) => r.status === 200 });
}
